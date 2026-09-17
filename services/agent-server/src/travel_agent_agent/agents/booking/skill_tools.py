# 文件职责：实现 BookingAgent 的技能懒加载与受控 shell 命令执行。
# 定义 SkillRepository、SkillTools 与命令校验函数，负责路径穿越防护、命令白名单、
# API Key 注入和输出脱敏，命令正文与输出不进入日志。
from __future__ import annotations

import asyncio
import os
import re
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any

from langchain_core.tools import StructuredTool
from travel_agent_sensitive_masker import SensitiveMasker

from travel_agent_agent.agents.itinerary_manage.client import TravelManageApiClient
from travel_agent_agent.core.settings import Settings

SKILLS_PACKAGE_ROOT = "skills"
MAX_SKILL_BYTES = 200_000
MAX_STDOUT_CHARS = 8000
MAX_STDERR_CHARS = 2000
DEFAULT_TIMEOUT_SECONDS = 30.0
# env 会把注入的 TUNIU_API_KEY 打印进模型上下文，因此不纳入白名单。
ALLOWED_COMMANDS = frozenset({"tuniu", "bash", "cat", "grep", "date", "which", "ls"})
# 这些命令的参数只能是打包技能目录内的路径，避免读取技能之外的任意文件。
PATH_SCOPED_COMMANDS = frozenset({"cat", "grep", "ls"})
ALLOWED_SKILL_SUFFIXES = frozenset({".md", ".json", ".txt"})
FORBIDDEN_SHELL_TOKENS = (";", "&&", "||", "|", "`", "$(", ">", "<", "\n", "\r")
# 容器已具备公网出口，禁止通过 cat/grep 直接读取挂载的 Docker Secret 文件。
FORBIDDEN_SHELL_FRAGMENTS = ("/run/secrets",)
PATH_DENIED_HINT = "shell 命令只允许使用技能目录内的路径，例如 bash tuniu-cli/scripts/setup.sh"
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TUNIU_API_KEY_ENV = "TUNIU_API_KEY"
FLIGHT_API_KEY_PLACEHOLDER = "${FLIGHT_API_KEY}"


class SkillNotFoundError(RuntimeError):
    """表示技能名或技能内资源路径不合法或不存在。"""


class SkillRepository:
    """从包内 skills 目录按名加载 SKILL.md 与参考文档。"""

    def __init__(self, root: str = SKILLS_PACKAGE_ROOT) -> None:
        """保存技能根目录名，不在初始化阶段读取任何文件。"""
        self._root = PurePosixPath(root)

    def root_path(self) -> Path:
        """返回打包技能目录的绝对路径，供受控 shell 的路径校验与工作目录使用。"""
        return Path(str(files("travel_agent_agent").joinpath(str(self._root))))

    def list_skills(self) -> tuple[str, ...]:
        """返回已打包的技能名列表。"""
        base = files("travel_agent_agent").joinpath(str(self._root))
        return tuple(
            sorted(
                item.name
                for item in base.iterdir()
                if item.is_dir() and SKILL_NAME_PATTERN.match(item.name)
            )
        )

    def load(self, skill_name: str, relative_path: str | None = None) -> str:
        """读取技能正文或技能内参考文件，拒绝路径穿越与非文本资源。"""
        if not SKILL_NAME_PATTERN.match(skill_name):
            raise SkillNotFoundError("skill_name_invalid")
        if skill_name not in self.list_skills():
            raise SkillNotFoundError("skill_not_found")
        target = PurePosixPath("SKILL.md") if not relative_path else PurePosixPath(relative_path)
        if target.is_absolute() or ".." in target.parts:
            raise SkillNotFoundError("skill_path_invalid")
        if target.suffix not in ALLOWED_SKILL_SUFFIXES:
            raise SkillNotFoundError("skill_resource_not_allowed")
        resource = files("travel_agent_agent").joinpath(
            str(self._root), skill_name, *target.parts
        )
        try:
            content = resource.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as error:
            raise SkillNotFoundError("skill_resource_not_found") from error
        if len(content.encode("utf-8")) > MAX_SKILL_BYTES:
            raise SkillNotFoundError("skill_resource_too_large")
        return content


class SkillTools:
    """提供技能懒加载与受控 shell 执行工具。"""

    def __init__(
        self,
        settings: Settings,
        client: TravelManageApiClient,
        repository: SkillRepository | None = None,
    ) -> None:
        """保存内部客户端、服务端密钥路径与技能仓库。"""
        self.settings = settings
        self.client = client
        self.repository = repository or SkillRepository()
        self.masker = SensitiveMasker()

    async def load_skill_through_path(
        self, skill: str, path: str | None = None
    ) -> dict[str, Any]:
        """按技能名懒加载 SKILL.md 或指定参考文档正文。"""
        try:
            content = self.repository.load(skill, path)
        except SkillNotFoundError as error:
            return {
                "success": False,
                "error_code": str(error),
                "available_skills": list(self.repository.list_skills()),
            }
        return {
            "success": True,
            "skill": skill,
            "path": path or "SKILL.md",
            "content": content,
        }

    async def execute_shell_command(
        self, command: str, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        """在命令白名单内执行技能命令，并注入当前用户 API Key。"""
        normalized = command.strip() if isinstance(command, str) else ""
        if not normalized:
            return {"success": False, "error_code": "command_required"}
        rejected = _reject_unsafe_command(normalized, self.repository.root_path())
        if rejected is not None:
            if rejected in {"command_path_denied", "command_option_denied"}:
                return {"success": False, "error_code": rejected, "hint": PATH_DENIED_HINT}
            return {"success": False, "error_code": rejected}
        prepared, error_code = await self._prepare_command(normalized)
        if error_code is not None:
            return {"success": False, "error_code": error_code}
        timeout = timeout_seconds if isinstance(timeout_seconds, (int, float)) else None
        timeout = float(timeout) if timeout and timeout > 0 else DEFAULT_TIMEOUT_SECONDS
        timeout = min(timeout, DEFAULT_TIMEOUT_SECONDS)
        return await self._run(prepared, timeout)

    async def _prepare_command(self, command: str) -> tuple[str, str | None]:
        """注入途牛环境变量与机票占位符密钥，密钥缺失时明确拒绝。"""
        prepared = command
        if FLIGHT_API_KEY_PLACEHOLDER in prepared:
            flight_key = await self._resolve_api_key("flight-manager")
            if not flight_key:
                return prepared, "flight_api_key_missing"
            prepared = prepared.replace(FLIGHT_API_KEY_PLACEHOLDER, flight_key)
        if "${" in prepared:
            return prepared, "command_placeholder_not_allowed"
        return prepared, None

    async def _run(self, command: str, timeout: float) -> dict[str, Any]:
        """以无 shell 拼接方式执行命令，并返回脱敏后的受限输出。"""
        env = os.environ.copy()
        if "tuniu" in command.split():
            api_key = await self._resolve_api_key("tuniu-cli")
            if api_key:
                env[TUNIU_API_KEY_ENV] = api_key
        try:
            process = await asyncio.create_subprocess_exec(
                "bash",
                "-lc",
                command,
                cwd=str(self.repository.root_path()),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError:
            return {"success": False, "error_code": "shell_unavailable"}
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return {"success": False, "error_code": "command_timeout"}
        return {
            "success": process.returncode == 0,
            "exit_code": process.returncode,
            "stdout": self.masker.mask_text(_decode(stdout))[:MAX_STDOUT_CHARS],
            "stderr": self.masker.mask_text(_decode(stderr))[:MAX_STDERR_CHARS],
        }

    async def _resolve_api_key(self, provider: str) -> str | None:
        """优先读取用户级 API Key，途牛可回退服务端 Secret。"""
        try:
            result = await self.client.request(
                "GET", f"/internal/v1/users/api-keys/{provider}/reveal"
            )
        except Exception:
            result = {}
        api_key = result.get("api_key") if isinstance(result, dict) else None
        if isinstance(api_key, str) and api_key.strip():
            return api_key.strip()
        if provider == "tuniu-cli":
            return _read_secret_file(self.settings.tuniu_api_key_file)
        return None

    def as_tools(self) -> list[StructuredTool]:
        """返回技能懒加载与受控 shell 执行工具。"""
        return [
            StructuredTool.from_function(
                coroutine=self.load_skill_through_path,
                name="load_skill_through_path",
                description=(
                    "按技能名懒加载技能正文，例如 tuniu-cli；需要参考文档时传 path，"
                    "如 references/flight.md。"
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.execute_shell_command,
                name="execute_shell_command",
                description=(
                    "执行白名单内的技能命令；命令首词只能是 "
                    "tuniu/bash/cat/grep/date/which/ls，路径只能是技能目录内的文件，"
                    "且不允许选项、管道、重定向和命令串联。"
                ),
            ),
        ]


def _reject_unsafe_command(
    command: str, skills_root: str | Path | None = None
) -> str | None:
    """校验首命令白名单、危险串联与路径范围；shell 只能访问打包技能目录。"""
    for token in FORBIDDEN_SHELL_TOKENS:
        if token in command:
            return "command_operator_denied"
    for fragment in FORBIDDEN_SHELL_FRAGMENTS:
        if fragment in command:
            return "command_secret_path_denied"
    parts = command.split()
    if not parts:
        return "command_required"
    head = parts[0]
    if head not in ALLOWED_COMMANDS:
        return "command_not_allowed"
    root = Path(skills_root) if skills_root is not None else SkillRepository().root_path()
    return _reject_out_of_scope_arguments(head, parts[1:], root)


def _reject_out_of_scope_arguments(head: str, args: list[str], root: Path) -> str | None:
    """只允许访问技能目录内的文件；bash 只允许执行技能目录内的脚本。"""
    if head == "bash":
        # 禁用 -c/-lc 等选项，避免借 bash 绕过命令白名单执行任意语句。
        if any(argument.startswith("-") for argument in args):
            return "command_option_denied"
        if len(args) != 1:
            return "command_path_denied"
        return None if _inside_skills(Path(args[0]), root, suffix=".sh") else "command_path_denied"
    if head not in PATH_SCOPED_COMMANDS:
        return None
    for argument in args:
        if argument.startswith("-") or not _looks_like_path(argument):
            continue
        if not _inside_skills(Path(argument), root):
            return "command_path_denied"
    return None


def _looks_like_path(value: str) -> bool:
    """判断参数是否被写成路径形式；普通关键字（如 which tuniu）不受路径限制。"""
    if value.startswith(("/", "\\", "~", "./", "../")):
        return True
    if "/" in value or "\\" in value:
        return True
    # Windows 盘符路径（如 C:\dir\file）在测试环境同样按路径处理。
    return len(value) > 1 and value[1] == ":" and value[0].isalpha()


def _inside_skills(candidate: Path, root: Path, suffix: str | None = None) -> bool:
    """判断候选路径解析后是否仍位于技能目录内。"""
    resolved_root = root.resolve()
    target = candidate if candidate.is_absolute() else resolved_root / candidate
    try:
        resolved = target.resolve()
    except OSError:
        return False
    if resolved != resolved_root and resolved_root not in resolved.parents:
        return False
    if suffix is not None and resolved.suffix != suffix:
        return False
    return resolved.is_file()


def _decode(payload: bytes | None) -> str:
    """解码子进程输出，非法字节按替换字符处理。"""
    if not payload:
        return ""
    return payload.decode("utf-8", errors="replace")


def _read_secret_file(path: str) -> str | None:
    """读取服务端 Secret 文件，文件缺失或为空时返回 None。"""
    try:
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError:
        return None
    return value or None
