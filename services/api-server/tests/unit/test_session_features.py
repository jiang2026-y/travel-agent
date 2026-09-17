# 文件职责：验证会话删除/改名、管理员审批决策与调试直达三个会话功能面接口。
# 定义服务层软删除与改名语义、审批决策流转与调试直达代理转发的用例。
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from travel_agent_api.api.errors import ServiceError
from travel_agent_api.api.routes.admin import (
    ApprovalDecisionRequest,
    DebugAgentRequest,
    debug_agent,
    decide_approval,
    list_approvals,
    list_debug_agents,
)
from travel_agent_api.application.auth_service import AuthenticatedUser
from travel_agent_api.core.correlation import CorrelationContext
from travel_agent_api.infrastructure.agent_client import AgentCommandError
from travel_agent_api.persistence.services import PostgresConversationService


class _ApprovalService:
    """审批服务替身，记录调用参数并返回固定结果。"""

    def __init__(self, *, decidable: bool = True) -> None:
        """保存是否可决策的标记。"""
        self.decidable = decidable
        self.filters: list[str | None] = []
        self.decisions: list[tuple[str, str, str | None]] = []

    async def list_approvals_for_admin(self, status: str | None = None) -> list[Any]:
        """记录状态过滤条件并返回一条审批记录。"""
        self.filters.append(status)
        return [_approval_row()]

    async def decide_approval(
        self, process_instance_id: str, decision: str, remark: str | None = None
    ) -> Any | None:
        """记录决策参数，按 decidable 返回记录或 None。"""
        self.decisions.append((process_instance_id, decision, remark))
        if not self.decidable:
            return None
        return _approval_row(status=decision, remark=remark)


class _AgentClient:
    """Agent 内部客户端替身，记录调试直达调用。"""

    def __init__(self, *, fail: bool = False) -> None:
        """保存是否模拟失败。"""
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def list_debug_agents(self, user: Any, correlation: Any) -> list[dict[str, Any]]:
        """返回固定智能体清单。"""
        del user, correlation
        return [{"name": "bookingAgent", "provider_key": "booking_agent", "enabled": True}]

    async def debug_agent(
        self, user: Any, correlation: Any, agent_name: str, message: str
    ) -> dict[str, Any]:
        """记录调用并按需抛出稳定错误。"""
        del user, correlation
        self.calls.append((agent_name, message))
        if self.fail:
            raise AgentCommandError("agent_command_rejected", False)
        return {"agent": agent_name, "assistant_reply": "调试回复", "pending_interaction": None}


class _AuditService:
    """审计替身，记录事件类型。"""

    def __init__(self) -> None:
        """初始化空事件列表。"""
        self.events: list[str] = []

    async def record(self, event_type: str, *_: object) -> None:
        """保存事件类型。"""
        self.events.append(event_type)


def _request(*, agent_client: Any | None = None, approvals: Any | None = None) -> Any:
    """构造仅暴露路由依赖字段的替身请求。"""
    state = SimpleNamespace(
        correlation_context=CorrelationContext("trace", "request", "run", "thread"),
        audit_service=_AuditService(),
        agent_client=agent_client or _AgentClient(),
        travel_order_service=approvals or _ApprovalService(),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), state=state)


def _admin() -> AuthenticatedUser:
    """构造管理员身份。"""
    return AuthenticatedUser(user_id="admin_1", account="admin", role="admin")


def _approval_row(
    status: str = "PENDING", remark: str | None = None
) -> Any:
    """构造审批记录替身。"""
    return SimpleNamespace(
        process_instance_id="proc_1",
        order_id="order_1",
        title="出差审批",
        status=status,
        remark=remark,
        submit_time=None,
        update_time=None,
    )


@pytest.mark.asyncio
async def test_admin_lists_and_decides_approval() -> None:
    """管理员可按状态查看审批并做出通过决定，返回不含表单正文的元数据。"""
    approvals = _ApprovalService()
    request = _request(approvals=approvals)

    listed = await list_approvals(request, status="PENDING", user=_admin())
    decided = await decide_approval(
        "proc_1",
        ApprovalDecisionRequest(decision="approve", remark="同意"),
        request,
        _admin(),
    )

    assert listed["approvals"][0]["process_instance_id"] == "proc_1"
    assert "approval_form" not in listed["approvals"][0]
    assert approvals.filters == ["PENDING"]
    assert approvals.decisions == [("proc_1", "APPROVED", "同意")]
    assert decided["status"] == "APPROVED"
    assert request.app.state.audit_service.events[-1] == "admin_approval_decided"


@pytest.mark.asyncio
async def test_admin_decision_conflict_is_rejected() -> None:
    """审批已终态或不存在时返回 409，并写入拒绝审计。"""
    request = _request(approvals=_ApprovalService(decidable=False))

    with pytest.raises(ServiceError) as captured:
        await decide_approval(
            "proc_1",
            ApprovalDecisionRequest(decision="reject"),
            request,
            _admin(),
        )

    assert captured.value.code == "approval_not_decidable"
    assert captured.value.status_code == 409
    assert request.app.state.audit_service.events == ["admin_approval_decision_denied"]


@pytest.mark.asyncio
async def test_debug_agent_proxy_returns_reply_and_records_audit() -> None:
    """调试直达把消息原样转发给指定智能体，并记录调用审计。"""
    agent_client = _AgentClient()
    request = _request(agent_client=agent_client)

    listed = await list_debug_agents(request, _admin())
    result = await debug_agent(
        "bookingAgent", DebugAgentRequest(message="查一下明天北京到上海的航班"), request, _admin()
    )

    assert listed["agents"][0]["name"] == "bookingAgent"
    assert agent_client.calls == [("bookingAgent", "查一下明天北京到上海的航班")]
    assert result["assistant_reply"] == "调试回复"
    assert request.app.state.audit_service.events[-1] == "admin_debug_agent_called"


@pytest.mark.asyncio
async def test_debug_agent_failure_is_wrapped() -> None:
    """内部调试调用被拒时返回稳定业务错误，不泄露内部细节。"""
    request = _request(agent_client=_AgentClient(fail=True))

    with pytest.raises(ServiceError) as captured:
        await debug_agent(
            "bookingAgent", DebugAgentRequest(message="测试"), request, _admin()
        )

    assert captured.value.code == "agent_command_rejected"
    assert captured.value.status_code == 409
    assert request.app.state.audit_service.events == ["admin_debug_agent_failed"]


def test_rename_and_delete_conversation_validation() -> None:
    """会话改名先校验标题长度，空标题直接拒绝，不产生数据库访问。"""
    service = PostgresConversationService(session_factory=None, encryption=None)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        asyncio.run(service.rename_conversation("user_1", "conv_1", "   "))
    with pytest.raises(ValueError):
        asyncio.run(service.rename_conversation("user_1", "conv_1", "x" * 65))


def test_message_feedback_value_validation() -> None:
    """反馈只接受 up/down/null，非法值在访问数据库前即被拒绝。"""
    service = PostgresConversationService(session_factory=None, encryption=None)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        asyncio.run(service.set_message_feedback("user_1", "conv_1", "msg_1", "thumbs"))


def test_message_summary_exposes_feedback() -> None:
    """消息摘要需要携带反馈字段，供前端回显点赞/点踩状态。"""
    from datetime import UTC, datetime

    from travel_agent_api.persistence.services import MessageSummary

    summary = MessageSummary(
        "msg_1", "run_1", "assistant", "内容", datetime.now(UTC), "up"
    )

    assert summary.feedback == "up"
