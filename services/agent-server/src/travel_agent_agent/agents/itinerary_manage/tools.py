# 文件职责：定义 ItineraryManageAgent 的差旅单、冲突、预订、政策和用户信息工具。
# 每个工具只通过 TravelManageApiClient 调用 API Server，写操作要求确认凭证并保留幂等键。
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import StructuredTool

from travel_agent_agent.agents.common.session_context import (
    get_session_context,
    set_base_city,
    set_profile_status,
    set_travel_order_id,
)
from travel_agent_agent.agents.common.travel_order_read_tools import (
    TravelOrderReadTools as SharedTravelOrderReadTools,
)
from travel_agent_agent.agents.itinerary_manage.client import TravelManageApiClient
from travel_agent_agent.agents.itinerary_manage.date_normalizer import (
    TravelDateError,
    TravelDateNormalizer,
)
from travel_agent_agent.agents.itinerary_manage.hitl_context import (
    get_tool_execution_authorization,
)
from travel_agent_agent.agents.itinerary_manage.schemas import (
    ApprovalStatusRecord,
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


class TravelOrderWriteTools:
    """提供差旅申请提交、取消、修改和审批状态查询工具。"""

    def __init__(self, client: TravelManageApiClient) -> None:
        self.client = client
        self.dates = TravelDateNormalizer()

    async def submit_travel_approval(self, payload: TravelOrderInput) -> SubmitTravelApprovalRecord:
        """提交差旅单和审批单，幂等键由本次调用生成。"""
        normalized = self._normalize_dates(payload.model_dump(exclude_none=True))
        existing = await self.client.request(
            "GET", "/internal/v1/travel-orders", params={"order_id": payload.order_id}
        )
        existing_orders = existing.get("orders", [])
        if existing_orders:
            order = existing_orders[0]
            set_travel_order_id(payload.order_id)
            return SubmitTravelApprovalRecord(
                success=True,
                order_id=order.get("order_id", payload.order_id),
                approval_id=order.get("approval_id"),
                order_status=order.get("status", "UNKNOWN"),
                approval_status="PENDING",
                idempotent=True,
                verified=True,
                message="差旅申请已存在，已返回原申请结果。",
            )
        authorization = get_tool_execution_authorization()
        result = await self.client.request(
            "POST",
            "/internal/v1/travel-orders",
            body=normalized,
            confirmation_token=authorization.confirmation_token,
            interaction_id=authorization.interaction_id,
            idempotency_key=authorization.interaction_id,
        )
        order = result.get("order", {})
        set_travel_order_id(payload.order_id)
        verification = await self.client.request(
            "GET",
            "/internal/v1/travel-orders",
            params={"order_id": payload.order_id},
        )
        verified_order = verification.get("orders", [])
        verified = bool(verified_order) and verified_order[0].get("status") == "SUBMITTED"
        return SubmitTravelApprovalRecord(
            success=result.get("status") == "submitted",
            order_id=order.get("order_id", payload.order_id),
            approval_id=order.get("approval_id"),
            order_status=order.get("status", "UNKNOWN"),
            approval_status="PENDING",
            idempotent=False,
            verified=verified,
            message="差旅申请已提交审批。",
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
        authorization = get_tool_execution_authorization()
        normalized = self._normalize_dates(payload.model_dump(exclude_none=True))
        result = await self.client.request(
            "PATCH",
            f"/internal/v1/travel-orders/{order_id}",
            body=normalized,
            confirmation_token=authorization.confirmation_token,
            interaction_id=authorization.interaction_id,
            idempotency_key=authorization.interaction_id,
        )
        order = result.get("order", {})
        return ModifyTravelOrderRecord(
            success=result.get("status") == "resubmitted",
            order_id=order.get("order_id", order_id),
            new_approval_id=order.get("approval_id"),
            updated_fields=list(payload.model_dump(exclude_none=True)),
            message="差旅申请已更新并重新提交审批。",
        )

    async def query_approval_status(
        self, process_instance_id: str | None = None
    ) -> ApprovalStatusRecord:
        """查询审批实例状态。"""
        result = await self.client.request(
            "GET", "/internal/v1/approvals", params={"process_instance_id": process_instance_id}
        )
        items = result.get("approvals", [])
        item = items[0] if items else {}
        return ApprovalStatusRecord(
            found=bool(items),
            process_instance_id=item.get("process_instance_id"),
            order_id=item.get("order_id"),
            status=item.get("status"),
            latest=process_instance_id is None,
            message="已返回审批状态。" if items else "未找到审批记录。",
        )

    def as_tools(self) -> list[StructuredTool]:
        """将写工具适配为 LangChain 工具。"""
        return [
            StructuredTool.from_function(
                coroutine=self.submit_travel_approval, name="submit_travel_approval",
                description="提交差旅申请；必须先完成冲突检查并取得用户确认。",
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
            StructuredTool.from_function(
                coroutine=self.query_approval_status, name="query_approval_status",
                description="只读查询审批状态。",
            ),
        ]

    def _normalize_dates(self, payload: dict[str, Any]) -> dict[str, Any]:
        """在工具调用前将相对日期转换为 API 只接受的 YYYY-MM-DD。"""
        normalized = dict(payload)
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


class _LegacyTravelOrderReadTools:
    """提供差旅单只读查询。"""

    def __init__(self, client: TravelManageApiClient) -> None:
        self.client = client

    def _legacy_shared_tools(self) -> list[StructuredTool]:
        """兼容旧导出，实际只委托共享只读工具。"""
        return SharedTravelOrderReadTools(self.client).as_tools()

    async def query_travel_order(
        self, order_id: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        """按 ID 或状态查询当前用户差旅单。"""
        return await self.client.request(
            "GET", "/internal/v1/travel-orders",
            params={"order_id": order_id, "status": status},
        )

    def as_tools(self) -> list[StructuredTool]:
        return [StructuredTool.from_function(
            coroutine=self.query_travel_order, name="query_travel_order",
            description="只读查询当前用户差旅单。",
        )]


class BookingReadTools:
    """提供机票、酒店和火车票内部预订记录查询。"""

    def __init__(self, client: TravelManageApiClient) -> None:
        self.client = client

    async def query_booking_record(
        self, booking_id: str | None = None, travel_order_id: str | None = None
    ) -> dict[str, Any]:
        """按预订号或差旅单号查询预订记录。"""
        return await self.client.request(
            "GET", "/internal/v1/bookings",
            params={"booking_id": booking_id, "travel_order_id": travel_order_id},
        )

    def as_tools(self) -> list[StructuredTool]:
        return [StructuredTool.from_function(
            coroutine=self.query_booking_record, name="query_booking_record",
            description="只读查询关联预订记录。",
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
        self, city: str, amount: float | None = None
    ) -> dict[str, Any]:
        return await self.client.request(
            "GET", "/internal/v1/policies", params={"city": city, "amount": amount}
        )

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

    async def query_user_base_location(self) -> UserBaseLocationRecord:
        """读取当前用户常驻城市，缺失时只返回 found=false。"""
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
                base_city=cached,
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
