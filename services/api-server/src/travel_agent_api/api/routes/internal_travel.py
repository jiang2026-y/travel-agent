# 文件职责：提供 Agent Server 调用的受保护差旅数据接口。
# 定义内部认证依赖、差旅单/审批/预订查询及差旅单提交和取消路由。
from __future__ import annotations

import secrets
from datetime import date
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from travel_agent_api.api.errors import ServiceError
from travel_agent_api.application.travel_order_service import TravelOrderPersistenceService
from travel_agent_api.application.travel_policy_service import (
    PolicyServiceError,
    TravelPolicyService,
)
from travel_agent_api.application.user_profile_service import UserProfileError, UserProfileService
from travel_agent_api.core.correlation import get_correlation_context
from travel_agent_api.infrastructure.agent_client import (
    AgentClient,
    AgentCommandError,
    InternalUserContext,
)
from travel_agent_api.persistence.models import ApprovalRecord, BookingRecord, TravelOrder
from travel_agent_api.persistence.services import PostgresUserApiKeyService

router = APIRouter(prefix="/internal/v1", tags=["internal-travel"])


class TravelOrderPayload(BaseModel):
    """校验差旅单创建所需的最小业务字段。"""

    model_config = ConfigDict(extra="forbid")
    order_id: str | None = Field(default=None, min_length=1, max_length=64)
    destination: str | None = Field(default=None, max_length=256)
    departure_city: str | None = Field(default=None, max_length=128)
    departure_date: date | None = None
    return_date: date | None = None
    purpose: str | None = Field(default=None, max_length=512)


class TravelOrderConflictRequest(BaseModel):
    """校验冲突检测所需的行程事实字段，不包含用户身份字段。"""

    model_config = ConfigDict(extra="forbid")

    departure_city: str | None = Field(default=None, max_length=128)
    destination: str | None = Field(default=None, max_length=256)
    departure_date: date | None = None
    return_date: date | None = None
    exclude_order_id: str | None = Field(default=None, max_length=64)


class UserContactUpdatePayload(BaseModel):
    """校验用户主动提供的档案局部更新，不接受身份或权限字段。"""

    model_config = ConfigDict(extra="forbid")

    chinese_name: str | None = Field(default=None, max_length=64)
    name_pinyin: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    id_type: str | None = Field(default=None, max_length=32)
    id_number: str | None = Field(default=None, max_length=32)
    gender: str | None = Field(default=None, max_length=4)

    @model_validator(mode="after")
    def require_one_field(self) -> UserContactUpdatePayload:
        """拒绝空更新，避免无意重写加密档案。"""
        if not any(value is not None for value in self.model_dump().values()):
            raise ValueError("profile_update_empty")
        return self


class UserBaseLocationUpdatePayload(BaseModel):
    """校验用户常驻城市更新。"""

    model_config = ConfigDict(extra="forbid")

    base_city: str = Field(min_length=1, max_length=128)


class BookingCreatePayload(BaseModel):
    """校验 Agent 保存 Provider 成功结果所需的脱敏订单字段。"""

    model_config = ConfigDict(extra="forbid")
    booking_id: str = Field(min_length=1, max_length=64)
    travel_order_id: str | None = Field(default=None, max_length=64)
    biz_type: str = Field(min_length=1, max_length=16)
    platform: str = Field(default="TUNIU", max_length=32)
    external_order_no: str | None = Field(default=None, max_length=128)
    status: str = Field(default="PAYMENT_PENDING", max_length=32)
    external_status: str | None = Field(default=None, max_length=64)
    payment_status: str | None = Field(default="PENDING", max_length=32)
    title: str | None = Field(default=None, max_length=256)
    total_amount: float | None = None
    currency: str = Field(default="CNY", max_length=8)
    contact_name: str | None = Field(default=None, max_length=64)
    contact_phone: str | None = Field(default=None, max_length=32)
    detail: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = Field(default=None, max_length=512)


class BookingWriteAuthorizationPayload(BaseModel):
    """校验途牛写操作在外部调用前消费确认凭证所需的最小上下文。"""

    model_config = ConfigDict(extra="forbid")
    tool_name: str = Field(pattern=r"^(create_tuniu_(flight|train|hotel)_order|cancel_booking)$")
    order_args: str = Field(min_length=2, max_length=20000)
    travel_order_id: str | None = Field(default=None, max_length=64)


class ApiKeySavePayload(BaseModel):
    """校验用户提交的第三方 API Key，仅保存密文。"""

    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=8, max_length=512)


_API_KEY_PROVIDERS = ("tuniu-cli", "flight-manager")


async def require_agent_call(
    request: Request,
    authorization: str | None = Header(default=None),
    x_internal_user_id: str | None = Header(default=None),
    x_internal_user_role: str | None = Header(default=None),
) -> dict[str, str]:
    """校验 Docker Secret 内部 Token 与 API 传递的用户上下文。"""
    token_file = request.app.state.settings.api_agent_token_file
    try:
        expected = Path(token_file).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ServiceError("internal_token_unavailable", "内部服务暂不可用", True, 503) from error
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not expected or not secrets.compare_digest(supplied, expected):
        raise ServiceError("internal_auth_failed", "内部调用未通过认证", False, 401)
    if not x_internal_user_id or x_internal_user_role not in {"user", "admin"}:
        raise ServiceError("internal_user_context_required", "缺少内部用户上下文", False, 400)
    return {"user_id": x_internal_user_id, "role": x_internal_user_role}


def _service(request: Request) -> TravelOrderPersistenceService:
    """获取已配置 PostgreSQL Session 的差旅服务。"""
    service = getattr(request.app.state, "travel_order_service", None)
    if service is None:
        raise ServiceError("persistence_not_enabled", "持久化服务尚未启用", False, 503)
    return cast(TravelOrderPersistenceService, service)


def _profile_service(request: Request) -> UserProfileService:
    """获取用户档案服务，未启用持久化时明确拒绝。"""
    service = getattr(request.app.state, "user_profile_service", None)
    if service is None:
        raise ServiceError("persistence_not_enabled", "持久化服务尚未启用", False, 503)
    return cast(UserProfileService, service)


def _policy_service(request: Request) -> TravelPolicyService:
    """获取政策查询服务，未启用持久化时明确拒绝。"""
    service = getattr(request.app.state, "travel_policy_service", None)
    if service is None:
        raise ServiceError("persistence_not_enabled", "持久化服务尚未启用", False, 503)
    return cast(TravelPolicyService, service)


async def _record_profile_audit(
    request: Request, user_id: str, event_type: str, outcome: str, updated_fields: list[str]
) -> None:
    """写入不包含敏感字段值的档案操作审计。"""
    context = get_correlation_context(request)
    await request.app.state.audit_service.record(
        event_type,
        user_id,
        outcome,
        context.trace_id,
        context.request_id,
        context.run_id,
        context.thread_id,
        {"updated_fields": updated_fields},
    )


def _profile_error(error: UserProfileError) -> ServiceError:
    """将档案错误转换为不泄露敏感值的安全内部 API 错误。"""
    if str(error) == "profile_not_found":
        return ServiceError("profile_not_found", "未找到用户档案", False, 404)
    if str(error) == "profile_decryption_failed":
        return ServiceError("profile_unavailable", "用户档案暂不可读取", True, 503)
    return ServiceError("profile_update_invalid", "提供的档案信息格式无效", False, 422)


def _agent_client(request: Request) -> AgentClient:
    """获取 API Server 到 Agent Server 的受保护内部客户端。"""
    return cast(AgentClient, request.app.state.agent_client)


async def _consume_write_authorization(
    request: Request,
    identity: dict[str, str],
    *,
    confirmation_token: str | None,
    interaction_id: str | None,
    conversation_id: str | None,
    tool_name: str,
    tool_args: dict[str, Any],
) -> str:
    """在业务写事务前由 Agent Redis 原子消费绑定单次工具调用的确认 Token。"""
    if not confirmation_token or not interaction_id or not conversation_id:
        raise ServiceError("confirmation_required", "该操作需要有效确认", False, 409)
    try:
        return await _agent_client(request).consume_hitl_token(
            InternalUserContext(identity["user_id"], identity["role"], "active"),
            get_correlation_context(request),
            conversation_id,
            interaction_id,
            confirmation_token,
            tool_name,
            tool_args,
        )
    except AgentCommandError as error:
        status = 503 if error.retryable else 409
        raise ServiceError(error.code, "确认校验未通过", error.retryable, status) from error


def _order_payload(order: TravelOrder) -> dict[str, Any]:
    """将 SQLModel 差旅单转换为不含内部实现细节的 JSON。"""
    return {
        "order_id": order.order_id,
        "user_id": order.user_id,
        "destination": order.destination,
        "departure_city": order.departure_city,
        "departure_date": order.departure_date.isoformat() if order.departure_date else None,
        "return_date": order.return_date.isoformat() if order.return_date else None,
        "purpose": order.purpose,
        "status": order.status,
        "approval_id": order.approval_id,
    }


@router.post("/travel-orders")
async def submit_travel_order(
    payload: TravelOrderPayload,
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
    x_idempotency_key: str | None = Header(default=None),
    x_confirmation_token: str | None = Header(default=None),
    x_hitl_interaction_id: str | None = Header(default=None),
    x_conversation_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """以幂等键和一次性确认凭证创建差旅单及审批单。"""
    if (
        payload.departure_date
        and payload.return_date
        and payload.return_date < payload.departure_date
    ):
        raise ServiceError("invalid_date_range", "返程日期不能早于出发日期", False, 422)
    # 入库保留原始类型；确认校验单独用 JSON 模式导出（日期转 ISO 字符串）。
    request_payload = payload.model_dump(exclude_none=True)
    authorization_args = payload.model_dump(mode="json", exclude_none=True)
    data = dict(request_payload)
    data.pop("order_id", None)
    if not x_conversation_id:
        raise ServiceError("conversation_id_required", "缺少会话标识", False, 400)
    idempotency_key = await _consume_write_authorization(
        request,
        identity,
        confirmation_token=x_confirmation_token,
        interaction_id=x_hitl_interaction_id,
        conversation_id=x_conversation_id,
        tool_name="submit_travel_approval",
        tool_args={"payload": authorization_args},
    )
    if x_idempotency_key and x_idempotency_key != idempotency_key:
        raise ServiceError("idempotency_key_mismatch", "幂等键校验失败", False, 403)
    # 单号面向用户展示：未显式传入时生成可读单号，确认交互标识只用于幂等与审计。
    order_id = payload.order_id or f"order_{uuid4().hex[:16]}"
    order = await _service(request).submit(
        user_id=identity["user_id"],
        payload=data,
        idempotency_key=order_id,
    )
    return {"order": _order_payload(order), "status": "submitted"}


@router.get("/travel-orders")
async def list_travel_orders(
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
    status: str | None = None,
    order_id: str | None = None,
    departure_city: str | None = None,
    destination: str | None = None,
    departure_date_from: date | None = None,
    departure_date_to: date | None = None,
    return_date_from: date | None = None,
    return_date_to: date | None = None,
) -> dict[str, Any]:
    """按当前用户查询差旅单，支持状态、行程要素与日期区间筛选。"""
    service = _service(request)
    if order_id:
        order = await service.get_order(identity["user_id"], order_id)
        return {"orders": [] if order is None else [_order_payload(order)]}
    orders = await service.list_orders(identity["user_id"], status=status)
    orders = [
        item
        for item in orders
        if _matches_city(item.departure_city, departure_city)
        and _matches_city(item.destination, destination)
        and _matches_date_range(
            item, departure_date_from, departure_date_to, return_date_from, return_date_to
        )
    ]
    return {"orders": [_order_payload(item) for item in orders]}


def _matches_city(value: str | None, expected: str | None) -> bool:
    """按去除空白后的精确城市名匹配，未指定筛选时一律通过。"""
    if expected is None or not expected.strip():
        return True
    if value is None:
        return False
    return value.strip() == expected.strip()


def _matches_date_range(
    order: TravelOrder,
    departure_date_from: date | None,
    departure_date_to: date | None,
    return_date_from: date | None,
    return_date_to: date | None,
) -> bool:
    """判断差旅单是否满足可选的出返程日期范围。"""
    departure_after_start = (
        departure_date_from is None
        or order.departure_date is None
        or order.departure_date >= departure_date_from
    )
    departure_before_end = (
        departure_date_to is None
        or order.departure_date is None
        or order.departure_date <= departure_date_to
    )
    return_after_start = (
        return_date_from is None
        or order.return_date is None
        or order.return_date >= return_date_from
    )
    return_before_end = (
        return_date_to is None
        or order.return_date is None
        or order.return_date <= return_date_to
    )
    return (
        departure_after_start
        and departure_before_end
        and return_after_start
        and return_before_end
    )


@router.post("/travel-orders/{order_id}/cancel")
async def cancel_travel_order(
    order_id: str,
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
    x_confirmation_token: str | None = Header(default=None),
    x_hitl_interaction_id: str | None = Header(default=None),
    x_conversation_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """取消草稿/审批中/已通过差旅单并撤销待审批实例。"""
    if not x_conversation_id:
        raise ServiceError("conversation_id_required", "缺少会话标识", False, 400)
    await _consume_write_authorization(
        request,
        identity,
        confirmation_token=x_confirmation_token,
        interaction_id=x_hitl_interaction_id,
        conversation_id=x_conversation_id,
        tool_name="cancel_travel_order",
        tool_args={"order_id": order_id},
    )
    try:
        order = await _service(request).cancel(identity["user_id"], order_id)
    except ValueError as error:
        raise ServiceError(str(error), "当前状态不允许取消", False, 409) from error
    if order is None:
        raise ServiceError("travel_order_not_found", "差旅单不存在", False, 404)
    return {"order": _order_payload(order), "status": "cancelled"}


@router.patch("/travel-orders/{order_id}")
async def modify_travel_order(
    order_id: str,
    payload: TravelOrderPayload,
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
    x_confirmation_token: str | None = Header(default=None),
    x_hitl_interaction_id: str | None = Header(default=None),
    x_conversation_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """修改差旅单、撤销旧待审批实例并创建新的审批快照。"""
    # 入库保留原始类型；确认校验单独使用 JSON 可序列化的副本。
    request_payload = payload.model_dump(exclude_none=True)
    authorization_args = payload.model_dump(mode="json", exclude_none=True)
    data = dict(request_payload)
    data.pop("order_id", None)
    if not x_conversation_id:
        raise ServiceError("conversation_id_required", "缺少会话标识", False, 400)
    await _consume_write_authorization(
        request,
        identity,
        confirmation_token=x_confirmation_token,
        interaction_id=x_hitl_interaction_id,
        conversation_id=x_conversation_id,
        tool_name="modify_travel_order",
        tool_args={"order_id": order_id, "payload": authorization_args},
    )
    try:
        order = await _service(request).modify(
            user_id=identity["user_id"], order_id=order_id, payload=data
        )
    except ValueError as error:
        message = (
            "返程日期不能早于出发日期"
            if str(error) == "invalid_date_range"
            else "当前状态不允许修改"
        )
        raise ServiceError(str(error), message, False, 409) from error
    if order is None:
        raise ServiceError("travel_order_not_found", "差旅单不存在", False, 404)
    return {"order": _order_payload(order), "status": "resubmitted"}


@router.post("/travel-orders/conflicts")
async def check_conflicts(
    payload: TravelOrderConflictRequest,
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
) -> dict[str, Any]:
    """执行差旅单提交/修改前的服务端冲突规则，不依赖模型自行判断。"""
    if (
        payload.departure_date is None
        or payload.return_date is None
        or payload.return_date < payload.departure_date
    ):
        return {
            "valid": False,
            "has_conflict": False,
            "severity": "NONE",
            "summary": "行程日期不完整或范围无效",
            "conflicts": [],
        }
    return await _service(request).check_conflicts(
        user_id=identity["user_id"],
        departure_city=payload.departure_city,
        destination=payload.destination,
        departure_date=payload.departure_date,
        return_date=payload.return_date,
        exclude_order_id=payload.exclude_order_id,
    )


@router.get("/approvals")
async def list_approvals(
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
    process_instance_id: str | None = None,
) -> dict[str, Any]:
    """按当前用户查询审批状态。"""
    service = _service(request)
    if process_instance_id:
        approval = await service.get_approval(identity["user_id"], process_instance_id)
        rows = [] if approval is None else [approval]
    else:
        latest = await service.find_latest_approval(identity["user_id"])
        rows = [] if latest is None else [latest]
    return {"approvals": [_approval_payload(item) for item in rows]}


def _approval_payload(item: ApprovalRecord) -> dict[str, Any]:
    """序列化审批实例的公开字段。"""
    return {
        "process_instance_id": item.process_instance_id,
        "order_id": item.order_id,
        "status": item.status,
        "title": item.title,
        "submit_time": item.submit_time.isoformat() if item.submit_time else None,
    }


@router.get("/bookings")
async def list_bookings(
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
    travel_order_id: str | None = None,
    booking_id: str | None = None,
    biz_type: str | None = None,
) -> dict[str, Any]:
    """按用户和差旅关联条件查询预订记录。"""
    rows = await _service(request).list_bookings(
        identity["user_id"],
        travel_order_id=travel_order_id,
        booking_id=booking_id,
        biz_type=biz_type,
    )
    return {"bookings": [_booking_payload(item) for item in rows]}


@router.post("/bookings")
async def create_booking(
    payload: BookingCreatePayload,
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
    x_idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """仅供 Agent 在 Provider 成功后保存脱敏订单结果，浏览器不可直接调用。"""
    idempotency_key = x_idempotency_key or payload.booking_id
    if idempotency_key != payload.booking_id:
        raise ServiceError("idempotency_key_mismatch", "幂等键与订单号不一致", False, 403)
    booking = await _service(request).upsert_booking(
        user_id=identity["user_id"],
        payload=payload.model_dump(exclude_none=True),
        idempotency_key=idempotency_key,
    )
    return {"booking": _booking_payload(booking), "status": "saved"}


@router.post("/bookings/write-authorization")
async def authorize_booking_write(
    payload: BookingWriteAuthorizationPayload,
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
    x_confirmation_token: str | None = Header(default=None),
    x_hitl_interaction_id: str | None = Header(default=None),
    x_conversation_id: str | None = Header(default=None),
) -> dict[str, str]:
    """确认差旅审批已通过后，原子消费一次性凭证并返回预订幂等键。"""
    if payload.tool_name == "cancel_booking":
        if not payload.travel_order_id:
            records = await _service(request).list_bookings(
                identity["user_id"], booking_id=payload.order_args
            )
            if not records:
                raise ServiceError("booking_not_found", "预订记录不存在", False, 404)
            payload = payload.model_copy(update={"travel_order_id": records[0].travel_order_id})
    else:
        order = await _service(request).get_order(
            identity["user_id"], payload.travel_order_id or ""
        )
        if order is None:
            raise ServiceError("travel_order_not_found", "关联差旅单不存在", False, 404)
        if str(order.status) != "APPROVED":
            raise ServiceError(
                "travel_order_not_approved", "差旅单尚未审批通过，不能预订", False, 409
            )
    idempotency_key = await _consume_write_authorization(
        request,
        identity,
        confirmation_token=x_confirmation_token,
        interaction_id=x_hitl_interaction_id,
        conversation_id=x_conversation_id,
        tool_name=payload.tool_name,
        tool_args=(
            {"booking_id": payload.order_args}
            if payload.tool_name == "cancel_booking"
            else {"order_args": payload.order_args}
        ),
    )
    return {"idempotency_key": idempotency_key}


@router.post("/bookings/{booking_id}/provider-cancelled")
async def mark_provider_cancelled(
    booking_id: str,
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
) -> dict[str, Any]:
    """Provider 成功取消后更新内部订单，避免外部失败时修改本地状态。"""
    bookings = await _service(request).list_bookings(identity["user_id"], booking_id=booking_id)
    if not bookings:
        raise ServiceError("booking_not_found", "预订记录不存在", False, 404)
    booking = await _service(request).cancel_booking(identity["user_id"], booking_id)
    if booking is None:
        raise ServiceError("booking_not_found", "预订记录不存在", False, 404)
    return {"booking": _booking_payload(booking), "status": "booking_cancelled"}


@router.post("/bookings/{booking_id}/cancel")
async def cancel_booking(
    booking_id: str,
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
    x_confirmation_token: str | None = Header(default=None),
    x_hitl_interaction_id: str | None = Header(default=None),
    x_conversation_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """取消内部预订；外部机票和火车票 Provider 不在此接口执行。"""
    if not x_conversation_id:
        raise ServiceError("conversation_id_required", "缺少会话标识", False, 400)
    await _consume_write_authorization(
        request,
        identity,
        confirmation_token=x_confirmation_token,
        interaction_id=x_hitl_interaction_id,
        conversation_id=x_conversation_id,
        tool_name="cancel_booking",
        tool_args={"booking_id": booking_id},
    )
    bookings = await _service(request).list_bookings(identity["user_id"], booking_id=booking_id)
    if not bookings:
        raise ServiceError("booking_not_found", "预订记录不存在", False, 404)
    candidate = bookings[0]
    if candidate.biz_type in {"FLIGHT", "TRAIN"}:
        return {"status": "provider_disabled", "message": "外部交通预订取消能力当前未启用"}
    booking = await _service(request).cancel_booking(identity["user_id"], booking_id)
    if booking is None:
        raise ServiceError("booking_not_found", "预订记录不存在", False, 404)
    return {"booking": _booking_payload(booking), "status": "cancelled"}


@router.get("/policies")
async def query_policy(
    request: Request,
    city: str,
    identity: dict[str, str] = Depends(require_agent_call),
    amount: float | None = None,
    hotel_amount: float | None = None,
    hotel_star: int | None = None,
    flight_class: str | None = None,
    train_seat_class: str | None = None,
    daily_meal_amount: float | None = None,
    daily_transport_amount: float | None = None,
    departure_date: date | None = None,
) -> dict[str, Any]:
    """查询当前用户政策；提供动态参数时同时返回合规校验结果。"""
    values = {
        "amount": amount,
        "hotel_amount": hotel_amount,
        "hotel_star": hotel_star,
        "flight_class": flight_class,
        "train_seat_class": train_seat_class,
        "daily_meal_amount": daily_meal_amount,
        "daily_transport_amount": daily_transport_amount,
        "departure_date": departure_date,
    }
    try:
        result = await _policy_service(request).check_policy(identity["user_id"], city, values)
    except PolicyServiceError as error:
        if str(error) == "profile_not_found":
            raise ServiceError("profile_not_found", "未找到用户档案", False, 404) from error
        if str(error) == "profile_incomplete":
            raise ServiceError(
                "profile_incomplete", "缺少有效职级，无法匹配差旅政策", False, 422
            ) from error
        if str(error) == "city_required":
            raise ServiceError("city_required", "缺少目的地城市", False, 422) from error
        raise ServiceError("policy_not_found", "未找到匹配的差旅政策", False, 404) from error
    result["city"] = city
    return result


@router.get("/users/contact")
async def query_contact(
    request: Request, identity: dict[str, str] = Depends(require_agent_call)
) -> dict[str, Any]:
    """仅返回当前用户标识，避免向模型暴露高敏感联系方式。"""
    try:
        return await _profile_service(request).contact_info(identity["user_id"])
    except UserProfileError as error:
        raise _profile_error(error) from error


@router.get("/users/base-location")
async def query_base_location(
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
) -> dict[str, Any]:
    """返回当前用户基础地点的安全占位结果。"""
    try:
        return await _profile_service(request).base_location(identity["user_id"])
    except UserProfileError as error:
        raise _profile_error(error) from error


@router.patch("/users/contact")
async def update_contact(
    payload: UserContactUpdatePayload,
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
) -> dict[str, Any]:
    """更新当前用户主动提供的档案字段，并仅审计更新字段名。"""
    try:
        result = await _profile_service(request).update_contact(
            identity["user_id"], payload.model_dump(exclude_none=True)
        )
    except UserProfileError as error:
        await _record_profile_audit(
            request, identity["user_id"], "profile_contact_update", "failed", []
        )
        raise _profile_error(error) from error
    await _record_profile_audit(
        request,
        identity["user_id"],
        "profile_contact_update",
        "success",
        cast(list[str], result["updatedFields"]),
    )
    return result


@router.patch("/users/base-location")
async def update_base_location(
    payload: UserBaseLocationUpdatePayload,
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
) -> dict[str, Any]:
    """更新当前用户常驻城市，不允许通过请求指定其他用户。"""
    try:
        result = await _profile_service(request).update_base_location(
            identity["user_id"], payload.base_city
        )
    except UserProfileError as error:
        await _record_profile_audit(
            request, identity["user_id"], "profile_base_city_update", "failed", []
        )
        raise _profile_error(error) from error
    await _record_profile_audit(
        request,
        identity["user_id"],
        "profile_base_city_update",
        "success",
        cast(list[str], result["updatedFields"]),
    )
    return result


def _booking_payload(item: BookingRecord) -> dict[str, Any]:
    """序列化预订记录并保留外部状态原值。"""
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


@router.get("/users/api-keys/{provider}")
async def get_api_key_status(
    provider: str,
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
) -> dict[str, Any]:
    """只返回当前用户是否已配置指定 provider 的 API Key，不回显任何密钥内容。"""
    _require_api_key_provider(provider)
    service = _api_key_service(request)
    return {
        "provider": provider,
        "has_key": await service.has_key(identity["user_id"], provider),
    }


@router.get("/users/api-keys/{provider}/reveal")
async def reveal_api_key(
    provider: str,
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
) -> dict[str, Any]:
    """返回明文密钥供 Agent 注入 CLI；必须写审计且不缓存、不记录正文。"""
    _require_api_key_provider(provider)
    service = _api_key_service(request)
    api_key = await service.reveal(identity["user_id"], provider)
    if api_key is None:
        await _record_profile_audit(
            request, identity["user_id"], f"api_key_reveal:{provider}", "missing", []
        )
        raise ServiceError("api_key_not_configured", "尚未配置该服务的 API Key", False, 404)
    await _record_profile_audit(
        request, identity["user_id"], f"api_key_reveal:{provider}", "success", []
    )
    return {"provider": provider, "api_key": api_key}


@router.put("/users/api-keys/{provider}")
async def save_api_key(
    provider: str,
    payload: ApiKeySavePayload,
    request: Request,
    identity: dict[str, str] = Depends(require_agent_call),
) -> dict[str, Any]:
    """加密保存用户提交的 API Key，审计只记录 provider 与结果。"""
    _require_api_key_provider(provider)
    service = _api_key_service(request)
    await service.save(identity["user_id"], provider, payload.api_key.strip())
    await _record_profile_audit(
        request, identity["user_id"], f"api_key_save:{provider}", "success", []
    )
    return {"provider": provider, "status": "saved"}


def _require_api_key_provider(provider: str) -> None:
    """只允许已登记的 provider，避免任意键名写入。"""
    if provider not in _API_KEY_PROVIDERS:
        raise ServiceError("api_key_provider_not_registered", "不支持的 API Key 服务", False, 404)


def _api_key_service(request: Request) -> PostgresUserApiKeyService:
    """读取用户 API Key 服务；未启用持久化时明确拒绝。"""
    service = getattr(request.app.state, "user_api_key_service", None)
    if service is None:
        raise ServiceError("persistence_not_enabled", "持久化服务尚未启用", False, 503)
    return cast(PostgresUserApiKeyService, service)
