# 本文件提供管理员状态、审计与审批决策路由。
# 定义 get_admin_status、list_audit_entries、list_approvals 与 decide_approval，
# 全部要求管理员身份并写入脱敏审计。
from __future__ import annotations

from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from travel_agent_api.api.dependencies import (
    record_audit_event,
    require_csrf,
    require_current_admin,
)
from travel_agent_api.api.errors import ServiceError
from travel_agent_api.application.auth_service import AuthenticatedUser
from travel_agent_api.application.travel_order_service import TravelOrderPersistenceService
from travel_agent_api.core.correlation import get_correlation_context
from travel_agent_api.infrastructure.agent_client import (
    AgentCommandError,
    InternalUserContext,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class ApprovalDecisionRequest(BaseModel):
    """校验管理员审批决定，只接受通过/驳回与可选备注。"""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    remark: str | None = Field(default=None, max_length=512)


def _travel_order_service(request: Request) -> TravelOrderPersistenceService:
    """获取差旅持久化服务，未启用时返回 503 而不是静默空结果。"""
    service = getattr(request.app.state, "travel_order_service", None)
    if service is None:
        raise ServiceError("persistence_not_enabled", "持久化服务尚未启用", False, 503)
    return cast(TravelOrderPersistenceService, service)


@router.get("/status")
async def get_admin_status(
    request: Request, user: AuthenticatedUser = Depends(require_current_admin)
) -> dict[str, str]:
    """返回不包含审计正文或其他用户数据的管理员访问状态。"""
    await record_audit_event(request, "admin_status_read", user.user_id, "success")
    return {"role": user.role, "status": "authorized"}


@router.get("/audit")
async def list_audit_entries(
    request: Request, user: AuthenticatedUser = Depends(require_current_admin)
) -> dict[str, object]:
    """返回仅含元数据的 P0 内存审计快照，并审计本次管理员查询。"""
    entries = await request.app.state.audit_service.list_entries()
    await record_audit_event(request, "admin_audit_read", user.user_id, "success")
    return {
        "entries": [
            {
                "event_type": entry.event_type,
                "actor_user_id": entry.actor_user_id,
                "outcome": entry.outcome,
                "trace_id": entry.trace_id,
                "request_id": entry.request_id,
                "run_id": entry.run_id,
                "thread_id": entry.thread_id,
                "created_at": entry.created_at.isoformat(),
            }
            for entry in entries
        ]
    }


@router.get("/approvals")
async def list_approvals(
    request: Request,
    status: str | None = None,
    user: AuthenticatedUser = Depends(require_current_admin),
) -> dict[str, object]:
    """管理员按状态查看审批实例，仅返回审批元数据。"""
    rows = await _travel_order_service(request).list_approvals_for_admin(status)
    await record_audit_event(request, "admin_approvals_read", user.user_id, "success")
    return {"approvals": [_approval_payload(row) for row in rows]}


@router.post("/approvals/{process_instance_id}/decision", dependencies=[Depends(require_csrf)])
async def decide_approval(
    process_instance_id: str,
    payload: ApprovalDecisionRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_current_admin),
) -> dict[str, object]:
    """管理员对审批实例做出通过或驳回决定，并写入审计。"""
    decided = await _travel_order_service(request).decide_approval(
        process_instance_id,
        "APPROVED" if payload.decision == "approve" else "REJECTED",
        payload.remark,
    )
    if decided is None:
        await record_audit_event(request, "admin_approval_decision_denied", user.user_id, "denied")
        raise ServiceError(
            "approval_not_decidable", "审批不存在或已完成决策", False, 409
        )
    await record_audit_event(request, "admin_approval_decided", user.user_id, "success")
    return _approval_payload(decided)


def _approval_payload(row: Any) -> dict[str, object]:
    """序列化审批实例的公开字段，不返回审批表单正文。"""
    return {
        "process_instance_id": row.process_instance_id,
        "order_id": row.order_id,
        "title": row.title,
        "status": row.status,
        "remark": row.remark,
        "submit_time": row.submit_time.isoformat() if row.submit_time else None,
        "update_time": row.update_time.isoformat() if row.update_time else None,
    }


class DebugAgentRequest(BaseModel):
    """校验管理员调试直达智能体的消息体。"""

    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8000)


@router.get("/debug/agents")
async def list_debug_agents(
    request: Request,
    user: AuthenticatedUser = Depends(require_current_admin),
) -> dict[str, object]:
    """列出可调试直达的智能体，便于管理员验证单个智能体行为。"""
    correlation = get_correlation_context(request)
    agents = await request.app.state.agent_client.list_debug_agents(
        InternalUserContext(user.user_id, user.role, "active"), correlation
    )
    await record_audit_event(request, "admin_debug_agents_read", user.user_id, "success")
    return {"agents": agents}


@router.post("/debug/agents/{agent_name}", dependencies=[Depends(require_csrf)])
async def debug_agent(
    agent_name: str,
    payload: DebugAgentRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_current_admin),
) -> dict[str, object]:
    """绕过意图识别把消息直接发给指定智能体，并返回其回复。"""
    correlation = get_correlation_context(request)
    try:
        result = await request.app.state.agent_client.debug_agent(
            InternalUserContext(user.user_id, user.role, "active"),
            correlation,
            agent_name,
            payload.message,
        )
    except AgentCommandError as error:
        await record_audit_event(request, "admin_debug_agent_failed", user.user_id, "failed")
        raise ServiceError(
            error.code,
            "调试请求未被智能体接受",
            error.retryable,
            503 if error.retryable else 409,
        ) from error
    await record_audit_event(request, "admin_debug_agent_called", user.user_id, "success")
    return dict(result)
