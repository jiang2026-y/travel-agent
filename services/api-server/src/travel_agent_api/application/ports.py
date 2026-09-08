# 本文件定义 API Server 的基础设施端口。
# 定义 SessionPort、RunPort 与 AgentCommandPort，用于隔离会话、运行索引和 Agent 调用实现。
from __future__ import annotations

from typing import Protocol


class SessionPort(Protocol):
    """定义服务端会话的最小读写边界。"""

    def create(self, user_id: str) -> str: ...

    def invalidate(self, session_id: str) -> None: ...


class RunPort(Protocol):
    """定义 Run 状态查询与保存边界，不绑定 PostgreSQL 实现。"""

    def save_status(self, run_id: str, status: str) -> None: ...

    def get_status(self, run_id: str) -> str | None: ...


class AgentCommandPort(Protocol):
    """定义 API 到 Agent 的命令边界，业务层不依赖 HTTP 客户端。"""

    async def send(self, command: dict[str, object]) -> dict[str, object]: ...
