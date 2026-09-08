# 本文件验证创建 Run 的后台 Agent 调度不会阻塞 HTTP 响应，并能回写运行状态与审计。
# 定义伪 Agent、会话服务和审计服务，覆盖成功和失败调度结果。
from __future__ import annotations

import pytest

from travel_agent_api.api.routes.conversations import _dispatch_start_run
from travel_agent_api.core.correlation import CorrelationContext
from travel_agent_api.infrastructure.agent_client import AgentRunResult, InternalUserContext
from travel_agent_api.persistence.services import RunSummary


class _AgentClient:
    """提供固定 Agent 调度结果。"""

    async def start_run(self, *_: object) -> AgentRunResult:
        """返回 Agent 已接收并进入运行中的结果。"""
        return AgentRunResult("running", "conv_001", "run_001", "thread_001", 1)


class _FailingAgentClient:
    """模拟后台 Agent 调度异常。"""

    async def start_run(self, *_: object) -> AgentRunResult:
        """触发后台失败路径。"""
        raise RuntimeError("agent_failure")


class _ConversationService:
    """记录后台任务要求写入的 Run 状态。"""

    def __init__(self) -> None:
        self.statuses: list[str] = []

    async def update_run_status(self, _: str, __: str, status: str) -> RunSummary:
        """保存状态并返回最小 Run 摘要。"""
        self.statuses.append(status)
        return RunSummary("run_001", "conv_001", "thread_001", status)


class _AuditService:
    """记录后台调度审计结果。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def record(self, event_type: str, _: str, outcome: str, *__: object) -> None:
        """保存事件类型和结果。"""
        self.events.append((event_type, outcome))


def _context() -> CorrelationContext:
    """构造稳定关联标识。"""
    return CorrelationContext("trace_001", "request_001", "run_001", "thread_001")


@pytest.mark.asyncio
async def test_background_dispatch_marks_run_running() -> None:
    """Agent 成功后后台任务应更新 Run 为运行中并写入成功审计。"""
    conversations = _ConversationService()
    audit = _AuditService()

    await _dispatch_start_run(
        _AgentClient(),
        conversations,
        audit,
        InternalUserContext("user_001", "user", "active"),
        _context(),
        "conv_001",
        "查询行程",
    )

    assert conversations.statuses == ["running"]
    assert audit.events == [("run_dispatched", "success")]


@pytest.mark.asyncio
async def test_background_dispatch_marks_run_failed() -> None:
    """Agent 异常不影响初始 202，但后台任务应把 Run 标记为失败。"""
    conversations = _ConversationService()
    audit = _AuditService()

    await _dispatch_start_run(
        _FailingAgentClient(),
        conversations,
        audit,
        InternalUserContext("user_001", "user", "active"),
        _context(),
        "conv_001",
        "查询行程",
    )

    assert conversations.statuses == ["failed"]
    assert audit.events == [("run_dispatch_failed", "failed")]
