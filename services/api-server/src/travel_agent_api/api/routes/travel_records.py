# 文件职责：提供当前登录用户的差旅单和预订记录只读查询接口。
# 定义 list_travel_orders、list_bookings 和序列化辅助函数，不直接执行 Provider 写操作。
from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, Request

from travel_agent_api.api.dependencies import get_current_user
from travel_agent_api.api.errors import ServiceError
from travel_agent_api.application.auth_service import AuthenticatedUser
from travel_agent_api.application.travel_order_service import TravelOrderPersistenceService
from travel_agent_api.persistence.models import ApprovalRecord, BookingRecord, TravelOrder

router = APIRouter(prefix="/api/v1", tags=["travel-records"])


def _service(request: Request) -> TravelOrderPersistenceService:
    """获取已启用的差旅持久化服务。"""
    service = getattr(request.app.state, "travel_order_service", None)
    if service is None:
        raise ServiceError("persistence_not_enabled", "持久化服务尚未启用", False, 503)
    return cast(TravelOrderPersistenceService, service)


@router.get("/travel-orders")
async def list_travel_orders(
    request: Request, user: AuthenticatedUser = Depends(get_current_user)
) -> dict[str, list[dict[str, Any]]]:
    """仅返回当前用户自己的差旅单。"""
    service = _service(request)
    rows = await service.list_orders(user.user_id)
    approvals = await service.list_approvals_by_process_instance_ids(
        user.user_id, [row.approval_id for row in rows]
    )
    return {
        "orders": [
            _order_payload(row, approvals.get(row.approval_id or "")) for row in rows
        ]
    }


@router.get("/bookings")
async def list_bookings(
    request: Request, user: AuthenticatedUser = Depends(get_current_user)
) -> dict[str, list[dict[str, Any]]]:
    """仅返回当前用户自己的预订记录和脱敏支付链接。"""
    rows = await _service(request).list_bookings(user.user_id)
    return {"bookings": [_booking_payload(row) for row in rows]}


def _order_payload(item: TravelOrder, approval: ApprovalRecord | None = None) -> dict[str, Any]:
    """序列化差旅单公开字段，并附带审批实例状态供“我的差旅”展示。"""
    return {
        "order_id": item.order_id,
        "destination": item.destination,
        "departure_city": item.departure_city,
        "departure_date": item.departure_date.isoformat() if item.departure_date else None,
        "return_date": item.return_date.isoformat() if item.return_date else None,
        "purpose": item.purpose,
        "status": item.status,
        "approval_id": approval.process_instance_id if approval else item.approval_id,
        "approval_status": approval.status if approval else None,
        "submitted_at": (
            approval.submit_time.isoformat() if approval and approval.submit_time else None
        ),
    }


def _booking_payload(item: BookingRecord) -> dict[str, Any]:
    """序列化预订状态、外部订单号和支付链接。"""
    return {
        "booking_id": item.booking_id,
        "travel_order_id": item.travel_order_id,
        "biz_type": item.biz_type,
        "platform": item.platform,
        "external_order_no": item.external_order_no,
        "status": item.status,
        "external_status": item.external_status,
        "payment_status": item.payment_status,
        "title": item.title,
        "total_amount": str(item.total_amount) if item.total_amount is not None else None,
        "payment_url": item.detail.get("payment_url") if isinstance(item.detail, dict) else None,
    }
