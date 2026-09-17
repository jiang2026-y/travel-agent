# 文件职责：提供 BookingAgent 的途牛查询、下单和取消工具。
# 定义 TuniuBookingTools，负责写前置确认、订单标准化和内部预订记录保存。
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool

from travel_agent_agent.agents.itinerary_manage.client import TravelManageApiClient
from travel_agent_agent.agents.itinerary_manage.hitl_context import (
    get_tool_execution_authorization,
)
from travel_agent_agent.core.settings import Settings
from travel_agent_agent.infrastructure.tuniu_client import (
    TuniuCliClient,
    TuniuProviderError,
    extract_external_order_no,
    extract_payment_url,
)


class TuniuBookingTools:
    """封装途牛机票、火车票和酒店的只读查询及确认后写操作。"""

    def __init__(self, settings: Settings, client: TravelManageApiClient) -> None:
        """保存内部 API 客户端与受控途牛 CLI 客户端。"""
        self.client = client
        self.enabled = settings.tuniu_call_enabled
        self.tuniu = TuniuCliClient(settings.tuniu_cli_command, settings.tuniu_api_key_file)

    async def search_flight(
        self, departure_city: str, arrival_city: str, departure_date: str
    ) -> dict[str, Any]:
        """查询机票最低价方案。"""
        return await self._call(
            "flight",
            "searchLowestPriceFlight",
            {
                "departureCityName": departure_city,
                "arrivalCityName": arrival_city,
                "departureDate": departure_date,
            },
        )

    async def search_train(
        self, departure_city: str, arrival_city: str, departure_date: str
    ) -> dict[str, Any]:
        """查询火车票最低价方案。"""
        return await self._call(
            "train",
            "searchLowestPriceTrain",
            {
                "departureCityName": departure_city,
                "arrivalCityName": arrival_city,
                "departureDate": departure_date,
            },
        )

    async def search_hotel(self, city_name: str) -> dict[str, Any]:
        """查询指定城市酒店。"""
        return await self._call("hotel", "tuniuHotelSearch", {"cityName": city_name})

    async def create_flight_order(self, order_args: str) -> dict[str, Any]:
        """在用户已确认具体方案后创建机票订单并返回支付链接。"""
        return await self._create_order("FLIGHT", "flight", "saveOrder", order_args)

    async def create_train_order(self, order_args: str) -> dict[str, Any]:
        """在用户已确认具体方案后创建火车票订单并返回支付链接。"""
        return await self._create_order("TRAIN", "train", "bookTrain", order_args)

    async def create_hotel_order(self, order_args: str) -> dict[str, Any]:
        """在用户已确认具体方案后创建酒店订单并返回支付链接。"""
        return await self._create_order("HOTEL", "hotel", "tuniuHotelCreateOrder", order_args)

    async def _create_order(
        self, biz_type: str, server: str, tool: str, order_args: str
    ) -> dict[str, Any]:
        """消费确认凭证后下单，并把外部订单号落库为内部预订记录。"""
        authorization = get_tool_execution_authorization()
        try:
            arguments = json.loads(order_args)
        except json.JSONDecodeError as error:
            raise ValueError("booking_order_args_invalid") from error
        if not isinstance(arguments, dict):
            raise ValueError("booking_order_args_invalid")
        travel_order_id = arguments.get("travel_order_id")
        if not isinstance(travel_order_id, str) or not travel_order_id:
            raise ValueError("travel_order_id_required")
        await self.client.request(
            "POST",
            "/internal/v1/bookings/write-authorization",
            body={
                "tool_name": f"create_tuniu_{biz_type.lower()}_order",
                "order_args": order_args,
                "travel_order_id": travel_order_id,
            },
            confirmation_token=authorization.confirmation_token,
            interaction_id=authorization.interaction_id,
        )
        provider_arguments = dict(arguments)
        provider_arguments.pop("travel_order_id", None)
        result = await self._call(server, tool, provider_arguments)
        if result.get("status") == "provider_error":
            raise RuntimeError(str(result.get("error_code") or "tuniu_provider_call_failed"))
        external_order_no = extract_external_order_no(result)
        if not external_order_no:
            raise TuniuProviderError("tuniu_order_number_missing")
        booking_id = f"bk_tuniu_{external_order_no}"
        saved = await self.client.request(
            "POST",
            "/internal/v1/bookings",
            body={
                "booking_id": booking_id,
                "biz_type": biz_type,
                "platform": "TUNIU",
                "travel_order_id": travel_order_id,
                "conversation_id": self.client.context.conversation_id,
                "external_order_no": external_order_no,
                "status": "PAYMENT_PENDING",
                "payment_status": "PENDING",
                "total_amount": _amount(result),
                "detail": {
                    "provider": "tuniu",
                    "result_keys": sorted(result.keys()),
                    "payment_url": extract_payment_url(result),
                },
            },
            idempotency_key=booking_id,
        )
        return {
            "status": "payment_pending",
            "booking": saved.get("booking", {}),
            "external_order_no": external_order_no,
            "payment_url": extract_payment_url(result),
        }

    async def cancel_booking(self, booking_id: str) -> dict[str, Any]:
        """用户确认后取消机票或火车票，并同步内部记录状态。"""
        authorization = get_tool_execution_authorization()
        records = await self.client.request(
            "GET", "/internal/v1/bookings", params={"booking_id": booking_id}
        )
        bookings = records.get("bookings")
        if not isinstance(bookings, list) or not bookings:
            return {"status": "booking_not_found"}
        record = bookings[0]
        if str(record.get("biz_type", "")).upper() not in {"FLIGHT", "TRAIN"}:
            return {"status": "hotel_cancel_requires_hotel_contact"}
        external_order_no = record.get("external_order_no")
        if not isinstance(external_order_no, str) or not external_order_no:
            return {"status": "external_order_missing"}
        await self.client.request(
            "POST",
            "/internal/v1/bookings/write-authorization",
            body={
                "tool_name": "cancel_booking",
                "order_args": booking_id,
                "travel_order_id": record.get("travel_order_id"),
            },
            confirmation_token=authorization.confirmation_token,
            interaction_id=authorization.interaction_id,
        )
        server = "flight" if str(record.get("biz_type")).upper() == "FLIGHT" else "train"
        result = await self._call(server, "cancelOrder", {"orderId": external_order_no})
        if result.get("status") == "provider_error":
            return result
        return await self.client.request(
            "POST", f"/internal/v1/bookings/{booking_id}/provider-cancelled"
        )

    async def _call(self, server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """在途牛调用开关开启时执行 CLI，关闭时返回稳定错误。"""
        if not self.enabled:
            raise TuniuProviderError("tuniu_provider_disabled")
        try:
            return await self.tuniu.call(server, tool, arguments)
        except TuniuProviderError as error:
            return {
                "status": "provider_error",
                "error_code": error.code,
                "retryable": error.retryable,
            }

    def as_tools(self) -> list[StructuredTool]:
        """返回供 LangChain 注册的途牛工具集合。"""
        return [
            StructuredTool.from_function(
                coroutine=self.search_flight,
                name="search_tuniu_flight",
                description="查询途牛机票最低价方案。",
            ),
            StructuredTool.from_function(
                coroutine=self.search_train,
                name="search_tuniu_train",
                description="查询途牛火车票最低价方案。",
            ),
            StructuredTool.from_function(
                coroutine=self.search_hotel,
                name="search_tuniu_hotel",
                description="查询指定城市的途牛酒店。",
            ),
            StructuredTool.from_function(
                coroutine=self.create_flight_order,
                name="create_tuniu_flight_order",
                description="用户确认后创建机票订单，order_args 必须是 JSON 字符串。",
            ),
            StructuredTool.from_function(
                coroutine=self.create_train_order,
                name="create_tuniu_train_order",
                description="用户确认后创建火车票订单，order_args 必须是 JSON 字符串。",
            ),
            StructuredTool.from_function(
                coroutine=self.create_hotel_order,
                name="create_tuniu_hotel_order",
                description="用户确认后创建酒店订单，order_args 必须是 JSON 字符串。",
            ),
            StructuredTool.from_function(
                coroutine=self.cancel_booking,
                name="cancel_booking",
                description=(
                    "用户确认后取消途牛机票或火车票；传入内部预订单号 bk_xxx，"
                    "不要传外部单号。"
                ),
            ),
        ]


def _amount(payload: dict[str, Any]) -> float | None:
    """从 Provider 结果提取金额，不保存完整原始响应。"""
    for key in ("totalAmount", "amount", "payAmount", "price"):
        value = payload.get(key)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            continue
    return None
