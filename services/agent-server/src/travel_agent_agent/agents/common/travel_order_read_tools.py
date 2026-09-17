# 文件职责：提供差旅子 Agent 共用的只读差旅单与审批工具集合。
# 定义 TravelOrderReadTools，覆盖按行程要素查询、按单号查询、列表查询、审批状态查询、
# 预订前审批校验与出行时间有效性校验，全部只读且以会话身份为边界。
from __future__ import annotations

from datetime import date
from typing import Any

from langchain_core.tools import StructuredTool

from travel_agent_agent.agents.itinerary_manage.client import TravelManageApiClient

APPROVED_STATUS = "APPROVED"


class TravelOrderReadTools:
    """封装当前用户可见的差旅单与审批只读查询能力。"""

    def __init__(self, client: TravelManageApiClient) -> None:
        """保存内部 API 客户端，不在工具层保存用户密钥或原始响应。"""
        self.client = client

    async def query_travel_order(
        self,
        origin: str,
        destination: str,
        departure_date: str,
    ) -> dict[str, Any]:
        """按出发城市、目的地与出发日期查询唯一差旅单。"""
        response = await self.client.request(
            "GET",
            "/internal/v1/travel-orders",
            params={
                "departure_city": origin.strip(),
                "destination": destination.strip(),
                "departure_date_from": departure_date.strip(),
                "departure_date_to": departure_date.strip(),
            },
        )
        orders = _order_list(response)
        if not orders:
            return {
                "found": False,
                "message": "未查询到匹配的差旅单，请确认出发城市、目的地和出发日期是否正确。",
            }
        return {"found": True, **_flatten_order(orders[0])}

    async def query_travel_order_by_order_id(self, order_id: str) -> dict[str, Any]:
        """按差旅单号查询详情，供取消、修改与预订前核对。"""
        response = await self.client.request(
            "GET", "/internal/v1/travel-orders", params={"order_id": order_id.strip()}
        )
        orders = _order_list(response)
        if not orders:
            return {"found": False, "message": "未查询到该差旅单，请确认差旅单号是否正确。"}
        return {"found": True, **_flatten_order(orders[0])}

    async def query_travel_orders(
        self,
        status: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """按状态与出发日期区间查询当前用户的差旅单列表。"""
        params: dict[str, Any] = {"status": _clean(status)}
        if _clean(start_date):
            params["departure_date_from"] = _clean(start_date)
        if _clean(end_date):
            params["departure_date_to"] = _clean(end_date)
        response = await self.client.request("GET", "/internal/v1/travel-orders", params=params)
        orders = [_flatten_order(item) for item in _order_list(response)]
        if not orders:
            return {"orders": [], "count": 0, "message": "当前没有符合条件的差旅单。"}
        return {"orders": orders, "count": len(orders)}

    async def query_approval_status(
        self, process_instance_id: str | None = None
    ) -> dict[str, Any]:
        """按审批实例号查询审批状态；未提供时返回最近一条审批单。"""
        response = await self.client.request(
            "GET",
            "/internal/v1/approvals",
            params={"process_instance_id": _clean(process_instance_id)},
        )
        approvals = response.get("approvals")
        items = approvals if isinstance(approvals, list) else []
        if not items:
            return {"found": False, "message": "未找到审批记录。"}
        item = items[0] if isinstance(items[0], dict) else {}
        return {
            "found": True,
            "process_instance_id": item.get("process_instance_id"),
            "order_id": item.get("order_id"),
            "status": item.get("status"),
            "latest": process_instance_id is None,
            "message": "已返回审批状态。",
        }

    async def check_travel_order_approval(
        self, order_id: str | None = None
    ) -> dict[str, Any]:
        """预订前校验是否存在已审批通过的差旅单；未指定单号时取最近一张。"""
        order = await self._resolve_order(order_id, prefer_approved=True)
        if order is None:
            return {
                "approved": False,
                "message": (
                    "未查询到您需要预订的差旅单，请提供差旅单号，"
                    "或者告知我出发时间、出发地、目的地等信息。"
                ),
            }
        if str(order.get("status") or "").upper() != APPROVED_STATUS:
            return {
                "approved": False,
                "order_id": order.get("order_id"),
                "status": order.get("status"),
                "message": "该差旅单尚未完成审批，请先等待审批通过后再预订。",
            }
        return {
            "approved": True,
            "order_id": order.get("order_id"),
            "status": order.get("status"),
            "departure_city": order.get("departure_city"),
            "destination": order.get("destination"),
            "departure_date": order.get("departure_date"),
            "return_date": order.get("return_date"),
            "message": "已存在审批通过的差旅单，可以执行预订。",
        }

    async def check_travel_time_validity(
        self, order_id: str | None = None
    ) -> dict[str, Any]:
        """校验差旅单行程是否尚未开始，用于行程类操作前的只读前置检查。"""
        today = date.today()
        order = await self._resolve_order(order_id)
        if order is None:
            return {
                "valid": True,
                "today": today.isoformat(),
                "blocked_orders": [],
                "message": "当前未查询到差旅单，不影响继续处理。",
            }
        blocked = _travel_time_block_reason(order, today)
        if blocked is None:
            return {
                "valid": True,
                "today": today.isoformat(),
                "blocked_orders": [],
                "message": "差旅单日期有效，可继续处理。",
            }
        return {
            "valid": False,
            "today": today.isoformat(),
            "blocked_orders": [
                {
                    "order_id": order.get("order_id"),
                    "destination": order.get("destination"),
                    "departure_date": order.get("departure_date"),
                    "return_date": order.get("return_date"),
                    "reason": blocked,
                }
            ],
            "message": f"检测到{blocked}的差旅单，无法继续处理，请如实告知用户。",
        }

    def as_tools(self) -> list[StructuredTool]:
        """返回全部只读差旅单与审批工具。"""
        return [
            StructuredTool.from_function(
                coroutine=self.query_travel_order,
                name="query_travel_order",
                description="按出发城市、目的地和出发日期查询差旅单详情，用于预订前确认行程。",
            ),
            StructuredTool.from_function(
                coroutine=self.query_travel_order_by_order_id,
                name="query_travel_order_by_order_id",
                description="按差旅单号查询指定差旅单详情与当前状态。",
            ),
            StructuredTool.from_function(
                coroutine=self.query_travel_orders,
                name="query_travel_orders",
                description="查询当前用户差旅单列表，可按状态和出发日期区间过滤。",
            ),
            StructuredTool.from_function(
                coroutine=self.query_approval_status,
                name="query_approval_status",
                description="查询差旅审批状态；未提供审批实例号时返回最近一条审批单。",
            ),
            StructuredTool.from_function(
                coroutine=self.check_travel_order_approval,
                name="check_travel_order_approval",
                description=(
                    "预订前强制校验：确认是否存在已审批通过的差旅单。"
                    "未提供差旅单号时自动取最近一张。"
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.check_travel_time_validity,
                name="check_travel_time_validity",
                description="校验差旅单行程是否尚未开始或结束；未提供单号时自动取最近一张。",
            ),
        ]

    async def _resolve_order(
        self, order_id: str | None, *, prefer_approved: bool = False
    ) -> dict[str, Any] | None:
        """按单号或最近一张差旅单解析目标订单，只读取当前用户可见数据。"""
        if _clean(order_id):
            response = await self.client.request(
                "GET", "/internal/v1/travel-orders", params={"order_id": _clean(order_id)}
            )
            orders = _order_list(response)
            return orders[0] if orders else None
        response = await self.client.request("GET", "/internal/v1/travel-orders")
        orders = _order_list(response)
        if not orders:
            return None
        if prefer_approved:
            approved = [
                item
                for item in orders
                if str(item.get("status") or "").upper() == APPROVED_STATUS
            ]
            if approved:
                return _sort_by_departure(approved)[-1]
        return _sort_by_departure(orders)[-1]


def _order_list(response: dict[str, Any]) -> list[dict[str, Any]]:
    """从内部差旅接口响应中读取差旅单列表。"""
    orders = response.get("orders")
    if not isinstance(orders, list):
        return []
    return [item for item in orders if isinstance(item, dict)]


def _flatten_order(order: dict[str, Any]) -> dict[str, Any]:
    """输出不含用户标识的扁平差旅单字段，便于模型直接引用。"""
    return {
        "order_id": order.get("order_id"),
        "status": order.get("status"),
        "departure_city": order.get("departure_city"),
        "destination": order.get("destination"),
        "departure_date": order.get("departure_date"),
        "return_date": order.get("return_date"),
        "purpose": order.get("purpose"),
        "approval_id": order.get("approval_id"),
    }


def _sort_by_departure(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按出发日期升序排列，缺失日期的订单排在最后。"""
    return sorted(orders, key=lambda item: str(item.get("departure_date") or "9999-12-31"))


def _travel_time_block_reason(order: dict[str, Any], today: date) -> str | None:
    """判断行程是否已结束或已开始，返回中文拦截原因。"""
    return_date = _parse_date(order.get("return_date"))
    if return_date is not None and today > return_date:
        return f"行程已结束（返回日期 {order.get('return_date')} 已过）"
    departure_date = _parse_date(order.get("departure_date"))
    if departure_date is not None and today >= departure_date:
        return f"行程已开始（出发日期 {order.get('departure_date')} 已到达或已过）"
    return None


def _parse_date(value: object) -> date | None:
    """解析 ISO 日期字符串，格式非法时返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _clean(value: str | None) -> str | None:
    """清洗可选的字符串过滤条件。"""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()
