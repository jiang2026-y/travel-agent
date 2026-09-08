# 本文件定义 Agent Server 的可替换外部能力端口。
# 定义检查点、模型、记忆、RAG、Skill 和 Tool Gateway 端口。
# 这些端口用于隔离运行时依赖。
from __future__ import annotations

from typing import Protocol


class CheckpointPort(Protocol):
    """定义 Run 检查点保存和读取边界。"""

    def save(self, thread_id: str, state: dict[str, object]) -> None: ...

    def load(self, thread_id: str) -> dict[str, object] | None: ...


class ModelPort(Protocol):
    """定义模型生成边界，避免编排层直接绑定百炼 SDK。"""

    async def generate(self, prompt: str) -> str: ...


class MemoryPort(Protocol):
    """定义长期记忆读取边界。"""

    async def search(self, user_id: str, query: str) -> list[str]: ...


class RagPort(Protocol):
    """定义受权限约束的知识检索边界。"""

    async def retrieve(self, query: str) -> list[str]: ...


class SkillPort(Protocol):
    """定义已注册旅行 Skill 的调用边界。"""

    async def invoke(self, name: str, arguments: dict[str, object]) -> dict[str, object]: ...


class ToolGatewayPort(Protocol):
    """定义受限远程工具网关边界。"""

    async def execute(self, provider_key: str, operation_key: str) -> bytes: ...
