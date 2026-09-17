# 文件职责：验证技能懒加载与受控 shell 执行的路径穿越与命令白名单防护。
# 定义技能读取、越权路径拒绝和命令校验测试。
from __future__ import annotations

from typing import Any

import pytest

from travel_agent_agent.agents.booking.skill_tools import (
    SkillNotFoundError,
    SkillRepository,
    SkillTools,
    _reject_unsafe_command,
)
from travel_agent_agent.core.settings import Settings


class _FakeClient:
    """内部 API 替身，始终返回未配置的密钥。"""

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """返回空对象，避免测试访问内部服务。"""
        del method, path, kwargs
        return {}


def _settings(tmp_path) -> Settings:
    """构造最小 Agent 配置，途牛密钥文件不存在时走降级。"""
    return Settings.from_environment(
        {
            "TOOL_GATEWAY_BASE_URL": "http://tool-gateway:8002",
            "API_SERVER_BASE_URL": "http://api-server:8000",
            "TRAVEL_AGENT_REDIS_URL": "redis://redis:6379/0",
            "TRAVEL_AGENT_EXTERNAL_MODE": "real_tuniu",
            "TRAVEL_AGENT_TUNIU_APPROVED": "true",
            "TUNIU_API_KEY_FILE": str(tmp_path / "missing_tuniu_key"),
        }
    )


def test_repository_loads_packaged_skill_and_blocks_escape() -> None:
    """技能正文可懒加载，越权路径与非法资源类型必须拒绝。"""
    repository = SkillRepository()
    content = repository.load("tuniu-cli")
    assert "tuniu" in content.lower()
    with pytest.raises(SkillNotFoundError):
        repository.load("tuniu-cli", "../SKILL.md")
    with pytest.raises(SkillNotFoundError):
        repository.load("tuniu-cli", "scripts/setup.sh")
    with pytest.raises(SkillNotFoundError):
        repository.load("not-a-skill")


def test_unsafe_commands_are_rejected_before_execution() -> None:
    """命令串联、管道、重定向与非白名单命令都必须在执行前拒绝。"""
    assert _reject_unsafe_command("tuniu call flight searchLowestPriceFlight") is None
    assert _reject_unsafe_command("tuniu a | cat") == "command_operator_denied"
    assert _reject_unsafe_command("tuniu a && rm -rf /") == "command_operator_denied"
    assert _reject_unsafe_command("rm -rf /") == "command_not_allowed"
    assert _reject_unsafe_command("curl http://example.com") == "command_not_allowed"
    assert _reject_unsafe_command("   ") == "command_required"


def test_secret_paths_are_rejected_after_egress_is_enabled() -> None:
    """容器具备公网出口后，读取挂载密钥文件的命令必须在执行前拒绝。"""
    assert _reject_unsafe_command("cat /run/secrets/tuniu_api_key") == (
        "command_secret_path_denied"
    )
    assert _reject_unsafe_command("grep TUNIU /run/secrets/tuniu_api_key") == (
        "command_secret_path_denied"
    )
    assert _reject_unsafe_command("tuniu call hotel tuniuHotelSearch") is None


def test_env_is_not_whitelisted(tmp_path) -> None:
    """env 会把注入的途牛 Key 打印进上下文，因此必须不在白名单内。"""
    assert _reject_unsafe_command("env") == "command_not_allowed"
    assert _reject_unsafe_command("env TUNIU_API_KEY") == "command_not_allowed"


def test_shell_paths_are_limited_to_skills_directory(tmp_path) -> None:
    """cat/grep/ls 只能读取技能目录内文件，技能之外的路径必须拒绝。"""
    root = tmp_path / "skills"
    (root / "tuniu-cli" / "references").mkdir(parents=True)
    (root / "tuniu-cli" / "references" / "flight.md").write_text("flight", encoding="utf-8")
    secret = tmp_path / "outside.txt"
    secret.write_text("secret", encoding="utf-8")

    assert _reject_unsafe_command("cat tuniu-cli/references/flight.md", root) is None
    assert _reject_unsafe_command("ls tuniu-cli", root) is None
    assert _reject_unsafe_command("cat /etc/passwd", root) == "command_path_denied"
    assert _reject_unsafe_command(f"cat {secret}", root) == "command_path_denied"
    assert _reject_unsafe_command("cat ../outside.txt", root) == "command_path_denied"
    assert _reject_unsafe_command("grep key ../../etc/hosts", root) == "command_path_denied"


def test_bash_only_runs_scripts_inside_skills(tmp_path) -> None:
    """bash 只允许执行技能目录内的 .sh 脚本，禁止 -c 等选项绕过白名单。"""
    root = tmp_path / "skills"
    (root / "tuniu-cli" / "scripts").mkdir(parents=True)
    (root / "tuniu-cli" / "scripts" / "setup.sh").write_text("echo ok", encoding="utf-8")
    outside = tmp_path / "evil.sh"
    outside.write_text("echo evil", encoding="utf-8")

    assert _reject_unsafe_command("bash tuniu-cli/scripts/setup.sh", root) is None
    assert _reject_unsafe_command("bash -c 'rm -rf /'", root) == "command_option_denied"
    assert _reject_unsafe_command("bash -lc tuniu-cli/scripts/setup.sh", root) == (
        "command_option_denied"
    )
    assert _reject_unsafe_command(f"bash {outside}", root) == "command_path_denied"
    assert _reject_unsafe_command("bash ../setup.sh", root) == "command_path_denied"


def test_path_denied_result_carries_hint(tmp_path) -> None:
    """路径越界时返回稳定错误码并附带可复用的路径提示。"""
    import asyncio

    tools = SkillTools(_settings(tmp_path), _FakeClient())
    result = asyncio.run(tools.execute_shell_command("cat /etc/passwd"))

    assert result["success"] is False
    assert result["error_code"] == "command_path_denied"
    assert "技能目录" in str(result["hint"])


@pytest.mark.asyncio
async def test_execute_shell_command_blocks_denied_command(tmp_path) -> None:
    """被拒绝的命令不得进入子进程，只返回稳定错误码。"""
    tools = SkillTools(_settings(tmp_path), _FakeClient())
    denied = await tools.execute_shell_command("rm -rf /")
    assert denied == {"success": False, "error_code": "command_not_allowed"}
    empty = await tools.execute_shell_command("")
    assert empty == {"success": False, "error_code": "command_required"}


@pytest.mark.asyncio
async def test_missing_flight_api_key_is_reported(tmp_path) -> None:
    """机票密钥占位符在密钥缺失时必须拒绝执行，而不是把占位符交给 shell。"""
    tools = SkillTools(_settings(tmp_path), _FakeClient())
    result = await tools.execute_shell_command(
        "cat ${FLIGHT_API_KEY} && date"
    )
    assert result["success"] is False
