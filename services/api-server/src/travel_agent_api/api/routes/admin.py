# 本文件提供 P0 管理员状态路由。
# 定义 get_admin_status，用于验证管理员服务端权限和记录脱敏访问审计。
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from travel_agent_api.api.dependencies import record_audit_event, require_current_admin
from travel_agent_api.application.auth_service import AuthenticatedUser

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


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
