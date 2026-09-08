# 本文件实现 P0 内存业务审计服务。
# 定义 AuditEntry，用于保存脱敏认证安全事件；定义 InMemoryAuditService，用于记录和查询事件。
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """表示一条不包含凭据或原始请求正文的业务审计事实。"""

    event_type: str
    actor_user_id: str | None
    outcome: str
    trace_id: str
    request_id: str
    run_id: str | None
    thread_id: str | None
    created_at: datetime


@dataclass(slots=True)
class InMemoryAuditService:
    """保存 P0 测试与开发期审计事件，不连接日志平台或数据库。"""

    entries: list[AuditEntry] = field(default_factory=list)

    async def record(
        self,
        event_type: str,
        actor_user_id: str | None,
        outcome: str,
        trace_id: str,
        request_id: str,
        run_id: str | None = None,
        thread_id: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        """追加一条仅含关联标识和结果的审计事实。"""
        del summary
        self.entries.append(
            AuditEntry(
                event_type=event_type,
                actor_user_id=actor_user_id,
                outcome=outcome,
                trace_id=trace_id,
                request_id=request_id,
                run_id=run_id,
                thread_id=thread_id,
                created_at=datetime.now(UTC),
            )
        )

    async def list_entries(self, limit: int = 100) -> tuple[AuditEntry, ...]:
        """返回审计事实的只读快照，仅供受控管理员路由使用。"""
        return tuple(self.entries[-limit:])
