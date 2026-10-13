# 文件职责：定义 ItineraryManageAgent 的差旅单、冲突、预订、政策和用户信息工具。
# 每个工具只通过 TravelManageApiClient 调用 API Server，写操作要求确认凭证并保留幂等键；
# 定义 _require_text、_require_iso_date，用于写工具的必填与 ISO 日期校验。
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from langchain_core.tools import StructuredTool

from travel_agent_agent.agents.common.session_context import (
    get_session_context,
    set_base_city,
    set_profile_status,
    set_travel_order_id,
)
from travel_agent_agent.agents.itinerary_manage.client import (
    TravelManageApiClient,
    TravelOrderToolError,
)
from travel_agent_agent.agents.itinerary_manage.date_normalizer import (
    TravelDateError,
    TravelDateNormalizer,
)
from travel_agent_agent.agents.itinerary_manage.hitl_context import (
    get_tool_execution_authorization,
)
from travel_agent_agent.agents.itinerary_manage.schemas import (
    CancelTravelOrderRecord,
    ModifyTravelOrderRecord,
    SubmitTravelApprovalRecord,
    TravelOrderConflictRecord,
    TravelOrderConflictRequest,
    TravelOrderInput,
    UserBaseLocationRecord,
    UserBaseLocationUpdateRequest,
    UserContactInfoRecord,
    UserContactUpdateRecord,
    UserContactUpdateRequest,
)

_LOGGER = logging.getLogger("travel_agent_agent.tools.cache")
_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_text(value: str, label: str) -> str:
    """校验写工具必填文本字段，缺失时抛出可展示的参数错误。"""
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise TravelOrderToolError("INVALID_PARAM", f"{label}不能为空，请先向用户确认后再提交。")
    return text


def _require_iso_date(value: str, label: str) -> str:
    """校验日期为明确的 YYYY-MM-DD；不接受相对日期，避免与确认参数指纹不一致。"""
    text = value.strip() if isinstance(value, str) else ""
    if not _ISO_DATE_PATTERN.fullmatch(text):
        raise TravelOrderToolError(
            "INVALID_DATE",
            f"{label}格式不正确，必须为 YYYY-MM-DD（如 2026-09-18），"
            "请先按当前日期换算出明确日期后再提交。",
        )
    try:
        date.fromisoformat(text)
    except ValueError as error:
        raise TravelOrderToolError(
            "INVALID_DATE", f"{label}不是有效日期，请核对后重试。"
        ) from error
    return text


class TravelOrderWriteTools:
    """提供差旅申请提交、取消、修改和审批状态查询工具。"""

    def __init__(self, client: TravelManageApiClient) -> None:
        self.client = client

    async def submit_travel_approval(
        self,
        destination: str,
        departure_city: str,
        departure_date: str,
        return_date: str,
        purpose: str,
    ) -> SubmitTravelApprovalRecord:
        """提交差旅申请；单号由服务端生成，相同出行要素已存在时直接返回已有单。

        请求体只包含五个出行要素的原值，不加任何额外字段，保证与确认卡片签发时的
        参数指纹逐字一致；回查使用服务端返回的真实单号，验证失败即为硬失败。
        """
        body = {
            "destination": _require_text(destination, "目的地"),
            "departure_city": _require_text(departure_city, "出发城市"),
            "departure_date": _require_iso_date(departure_date, "出发日期"),
            "return_date": _require_iso_date(return_date, "返回日期"),
            "purpose": _require_text(purpose, "出差事由"),
        }
        if body["return_date"] < body["departure_date"]:
            raise TravelOrderToolError(
                "INVALID_DATE_RANGE", "返程日期不能早于出发日期，请先修正日期后再提交。"
            )
        authorization = get_tool_execution_authorization()
        result = await self.client.request(
            "POST",
            "/internal/v1/travel-orders",
            body=body,
            confirmation_token=authorization.confirmation_token,
            interaction_id=authorization.interaction_id,
            idempotency_key=authorization.interaction_id,
        )
        order = result.get("order")
        order = order if isinstance(order, dict) else {}
        order_id = order.get("order_id")
        if not isinstance(order_id, str) or not order_id:
            raise TravelOrderToolError(
                "VERIFY_FAILED", "提交结果缺少差旅单号，请稍后重试或联系管理员。"
            )
        duplicate = bool(result.get("duplicate"))
        set_travel_order_id(order_id)
        verification = await self.client.request(
            "GET",
            "/internal/v1/travel-orders",
            params={"order_id": order_id},
        )
        verified_orders = verification.get("orders")
        verified = (
            isinstance(verified_orders, list)
            and bool(verified_orders)
            and isinstance(verified_orders[0], dict)
            and verified_orders[0].get("status") == "SUBMITTED"
        )
        if not verified:
            raise TravelOrderToolError(
                "VERIFY_FAILED",
                "操作已执行但验证失败：差旅单查询不到或状态与预期不符，"
                "请如实告知用户并建议稍后重试或联系管理员。",
            )
        message = (
            f"检测到相同出行信息的差旅申请已存在（差旅单号：{order_id}），已直接返回该申请，"
            "未重复创建。"
            if duplicate
            else f"差旅申请已创建并提交审批，当前状态：待审批（差旅单号：{order_id}）。"
        )
        return SubmitTravelApprovalRecord(
            success=result.get("status") == "submitted",
            order_id=order_id,
            approval_id=order.get("approval_id"),
            order_status=order.get("status", "UNKNOWN"),
            approval_status="PENDING",
            idempotent=duplicate,
            message=message,
        )

    async def cancel_travel_order(self, order_id: str) -> CancelTravelOrderRecord:
        """取消允许取消的差旅单。"""
        authorization = get_tool_execution_authorization()
        result = await self.client.request(
            "POST", f"/internal/v1/travel-orders/{order_id}/cancel",
            confirmation_token=authorization.confirmation_token,
            interaction_id=authorization.interaction_id,
            idempotency_key=authorization.interaction_id,
        )
        order = result.get("order", {})
        return CancelTravelOrderRecord(
            success=result.get("status") == "cancelled",
            order_id=order.get("order_id", order_id),
            order_status=order.get("status", "UNKNOWN"),
            approval_id=order.get("approval_id"),
            approval_status="CANCELLED",
            message="差旅申请已取消。",
        )

    async def modify_travel_order(
        self, order_id: str, payload: TravelOrderInput
    ) -> ModifyTravelOrderRecord:
        """修改差旅字段并由服务端撤销旧审批、创建新审批。"""
        order_id = _require_text(order_id, "差旅单号")
        body = payload.model_dump(exclude_none=True)
        for field, label in (("departure_date", "出发日期"), ("return_date", "返回日期")):
            value = body.get(field)
            if value is None:
                continue
            if isinstance(value, date):
                body[field] = value.isoformat()
            else:
                body[field] = _require_iso_date(
                    value if isinstance(value, str) else str(value), label
                )
        authorization = get_tool_execution_authorization()
        result = await self.client.request(
            "PATCH",
            f"/internal/v1/travel-orders/{order_id}",
            body=body,
            confirmation_token=authorization.confirmation_token,
            interaction_id=authorization.interaction_id,
            idempotency_key=authorization.interaction_id,
        )
        order = result.get("order", {})
        return ModifyTravelOrderRecord(
            success=result.get("status") == "resubmitted",
            order_id=order.get("order_id", order_id),
            new_approval_id=order.get("approval_id"),
            updated_fields=list(body),
            message="差旅申请已更新并重新提交审批。",
        )

    def as_tools(self) -> list[StructuredTool]:
        """将写工具适配为 LangChain 工具。"""
        return [
            StructuredTool.from_function(
                coroutine=self.submit_travel_approval, name="submit_travel_approval",
                description=(
                    "提交差旅申请，参数为目的地、出发城市、出发日期、返回日期与出差事由；"
                    "必须先完成冲突检查并取得用户确认；日期必须为 YYYY-MM-DD，不接受相对日期；"
                    "不需要传单号，单号由系统生成后返回；相同出行要素的生效中申请已存在时"
                    "直接返回该申请而不重复创建。"
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.cancel_travel_order, name="cancel_travel_order",
                description="取消差旅申请；必须取得用户确认。",
            ),
            StructuredTool.from_function(
                coroutine=self.modify_travel_order,
                name="modify_travel_order",
                description="修改差旅申请；必须先完成冲突检查并取得用户确认。",
            ),
        ]


class TravelOrderConflictTools:
    """提供提交和修改前必须执行的差旅冲突检查。"""

    def __init__(self, client: TravelManageApiClient) -> None:
        self.client = client
        self.dates = TravelDateNormalizer()

    async def check_travel_order_conflicts(
        self, payload: TravelOrderConflictRequest
    ) -> TravelOrderConflictRecord:
        """调用服务端冲突检查接口；服务端负责最终裁决。"""
        try:
            normalized = self._normalize_conflict_dates(payload)
        except TravelDateError as error:
            return TravelOrderConflictRecord(
                valid=False,
                has_conflict=False,
                severity="NONE",
                summary=f"行程日期无法解析：{error}",
                conflicts=[],
            )
        result = await self.client.request(
            "POST", "/internal/v1/travel-orders/conflicts", body=normalized
        )
        return TravelOrderConflictRecord.model_validate(result)

    def _normalize_conflict_dates(
        self, payload: TravelOrderConflictRequest
    ) -> dict[str, Any]:
        """将冲突检测日期统一转换为 API 接收的 ISO 日期字符串。"""
        normalized = payload.model_dump(exclude_none=True)
        departure = normalized.get("departure_date")
        returning = normalized.get("return_date")
        if isinstance(departure, str) and isinstance(returning, str):
            date_range = self.dates.normalize_range(departure, returning)
            normalized["departure_date"] = date_range.departure_date.isoformat()
            normalized["return_date"] = date_range.return_date.isoformat()
        elif isinstance(departure, str):
            normalized["departure_date"] = self.dates.normalize(departure).isoformat()
        elif isinstance(returning, str):
            normalized["return_date"] = self.dates.normalize(returning).isoformat()
        return normalized

    def as_tools(self) -> list[StructuredTool]:
        """返回冲突检查 LangChain 工具。"""
        return [StructuredTool.from_function(
            coroutine=self.check_travel_order_conflicts, name="check_travel_order_conflicts",
            description="检查差旅时间、城市和路线冲突，提交或修改前必调。",
        )]


class BookingReadTools:
    """提供机票、酒店和火车票内部预订记录查询。"""

    def __init__(self, client: TravelManageApiClient) -> None:
        self.client = client

    async def query_booking_record(
        self,
        booking_id: str | None = None,
        travel_order_id: str | None = None,
        biz_type: str | None = None,
    ) -> dict[str, Any]:
        """按预订单号、差旅单号或业务类型查询预订记录。"""
        return await self.client.request(
            "GET", "/internal/v1/bookings",
            params={
                "booking_id": booking_id,
                "travel_order_id": travel_order_id,
                "biz_type": biz_type,
            },
        )

    def as_tools(self) -> list[StructuredTool]:
        return [StructuredTool.from_function(
            coroutine=self.query_booking_record, name="query_booking_record",
            description=(
                "只读查询关联预订记录，可按预订单号、差旅单号或业务类型"
                "（FLIGHT/HOTEL/TRAIN/TICKET/CRUISE/VACATION）过滤。"
            ),
        )]


class BookingWriteTools:
    """提供安全的内部预订取消占位工具，禁止真实外部平台写操作。"""

    def __init__(self, client: TravelManageApiClient) -> None:
        self.client = client

    async def cancel_booking(self, booking_id: str) -> dict[str, Any]:
        """酒店等内部记录可幂等取消，外部交通 Provider 由 API 层拒绝调用。"""
        authorization = get_tool_execution_authorization()
        return await self.client.request(
            "POST", f"/internal/v1/bookings/{booking_id}/cancel",
            confirmation_token=authorization.confirmation_token,
            interaction_id=authorization.interaction_id,
            idempotency_key=authorization.interaction_id,
        )

    def as_tools(self) -> list[StructuredTool]:
        return [StructuredTool.from_function(
            coroutine=self.cancel_booking, name="cancel_booking",
            description="取消预订；外部机票和火车票 Provider 当前禁用。",
        )]


class PolicyTools:
    """提供只读差旅政策查询和合规校验占位工具。"""

    def __init__(self, client: TravelManageApiClient) -> None:
        self.client = client

    async def query_travel_policy(self, city: str) -> dict[str, Any]:
        normalized_city = city.strip()
        context = get_session_context()
        async def load_policy() -> tuple[str | None, dict[str, Any]]:
            result = await self.client.request(
                "GET", "/internal/v1/policies", params={"city": normalized_city}
            )
            serialized = (
                json.dumps(result, ensure_ascii=False, sort_keys=True)
                if _cacheable_policy(result)
                else None
            )
            return serialized, result

        if context.user_id:
            result, hit = await context.tool_cache.load_travel_policy(normalized_city, load_policy)
            if hit:
                _log_cache_hit("travel_policy")
            return result or {}
        _, result = await load_policy()
        return result

    async def check_travel_policy(
        self,
        city: str,
        amount: float | None = None,
        hotel_amount: float | None = None,
        hotel_star: int | None = None,
        flight_class: str | None = None,
        train_seat_class: str | None = None,
        daily_meal_amount: float | None = None,
        daily_transport_amount: float | None = None,
        departure_date: str | None = None,
    ) -> dict[str, Any]:
        """校验具体预订或费用参数，动态结果不写入会话缓存。"""
        params = {
            "city": city,
            "amount": amount,
            "hotel_amount": hotel_amount,
            "hotel_star": hotel_star,
            "flight_class": flight_class,
            "train_seat_class": train_seat_class,
            "daily_meal_amount": daily_meal_amount,
            "daily_transport_amount": daily_transport_amount,
            "departure_date": departure_date,
        }
        return await self.client.request("GET", "/internal/v1/policies", params=params)

    def as_tools(self) -> list[StructuredTool]:
        return [
            StructuredTool.from_function(
                coroutine=self.query_travel_policy,
                name="query_travel_policy",
                description="查询城市差旅政策。",
            ),
            StructuredTool.from_function(
                coroutine=self.check_travel_policy,
                name="check_travel_policy",
                description="校验差旅政策合规性。",
            ),
        ]


class QueryUserInfoTools:
    """提供当前用户基础地点和联系信息查询。"""

    def __init__(self, client: TravelManageApiClient) -> None:
        self.client = client

    async def query_user_contact_info(self) -> UserContactInfoRecord:
        """读取预订资料完整度和缺失项，不读取敏感档案明文。"""
        context = get_session_context()

        async def load_contact() -> dict[str, Any]:
            return UserContactInfoRecord.model_validate(
                await self.client.request("GET", "/internal/v1/users/contact")
            ).model_dump(by_alias=True)

        if context.user_id:
            cached, hit = await context.tool_cache.load_user_contact_info(load_contact)
        else:
            cached, hit = await load_contact(), False
        if hit:
            _log_cache_hit("contact_info")
            result = UserContactInfoRecord.model_validate(cached)
        else:
            result = UserContactInfoRecord.model_validate(cached)
        set_profile_status(result.flight_complete, result.hotel_complete, result.train_complete)
        return result

    async def update_user_contact_info(
        self, payload: UserContactUpdateRequest
    ) -> UserContactUpdateRecord:
        """持久化用户主动提供或修正的档案字段，并刷新完整度。"""
        result = UserContactUpdateRecord.model_validate(
            await self.client.request(
                "PATCH",
                "/internal/v1/users/contact",
                body=payload.model_dump(exclude_none=True),
            )
        )
        context = get_session_context()
        if context.user_id:
            await context.tool_cache.invalidate_user_contact_info()
        set_profile_status(result.flight_complete, result.hotel_complete, result.train_complete)
        return result

    async def query_user_base_location(
        self, user_id: str | None = None
    ) -> UserBaseLocationRecord:
        """读取当前用户常驻城市；入参用户标识仅占位，始终以会话身份为准。"""
        del user_id
        context = get_session_context()

        async def load_location() -> str | None:
            result = UserBaseLocationRecord.model_validate(
                await self.client.request("GET", "/internal/v1/users/base-location")
            )
            return result.base_city

        if context.user_id:
            cached, hit = await context.tool_cache.load_user_base_location(load_location)
        else:
            cached, hit = await load_location(), False
        if hit:
            _log_cache_hit("base_location")
            result = UserBaseLocationRecord(
                found=True,
                baseCity=cached,
                message="已找到用户常驻城市",
            )
        else:
            result = UserBaseLocationRecord.model_validate(
                {
                    "found": bool(cached),
                    "baseCity": cached,
                    "message": "已找到用户常驻城市" if cached else "未找到用户常驻城市",
                }
            )
        set_base_city(result.base_city)
        return result

    async def update_user_base_location(
        self, payload: UserBaseLocationUpdateRequest
    ) -> UserBaseLocationRecord:
        """持久化用户主动更新的常驻城市并刷新当前上下文。"""
        result = UserBaseLocationRecord.model_validate(
            await self.client.request(
                "PATCH",
                "/internal/v1/users/base-location",
                body=payload.model_dump(),
            )
        )
        context = get_session_context()
        if context.user_id:
            await context.tool_cache.set_user_base_location(result.base_city)
        set_base_city(result.base_city)
        return result

    def as_tools(self) -> list[StructuredTool]:
        return [
            StructuredTool.from_function(
                coroutine=self.query_user_contact_info,
                name="query_user_contact_info",
                description="查询当前用户必要联系信息。",
            ),
            StructuredTool.from_function(
                coroutine=self.update_user_contact_info,
                name="update_user_contact_info",
                description="保存用户主动提供或修正的联系与乘机人资料，并返回更新后的完整度。",
            ),
            StructuredTool.from_function(
                coroutine=self.query_user_base_location,
                name="query_user_base_location",
                description="查询当前用户基础出发地。",
            ),
            StructuredTool.from_function(
                coroutine=self.update_user_base_location,
                name="update_user_base_location",
                description="保存用户主动更新的常驻城市。",
            ),
        ]

    def base_location_tools(self) -> list[StructuredTool]:
        """仅向行程规划 Agent 暴露常驻城市查询工具。"""
        return [
            StructuredTool.from_function(
                coroutine=self.query_user_base_location,
                name="query_user_base_location",
                description="查询当前用户常驻城市；仅在用户未说明出发地时使用。",
            )
        ]


def _cacheable_policy(result: dict[str, Any]) -> bool:
    """仅缓存包含实际政策内容的非空结果，避免将占位响应视为命中。"""
    policy = result.get("policy")
    return policy is not None and policy != "" and policy != {} and policy != []


def _log_cache_hit(cache_name: str) -> None:
    """记录不含城市、用户或档案内容的结构化缓存命中日志。"""
    _LOGGER.info(
        "tool_cache_hit event=tool_cache_hit cache_scope=session cache_name=%s", cache_name
    )


# 兼容旧导入名；实际暴露的只读工具实现统一来自 common.travel_order_read_tools。
