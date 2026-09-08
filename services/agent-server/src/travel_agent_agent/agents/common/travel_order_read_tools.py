# 文件职责：提供多个差旅子 Agent 共用的单一只读差旅单查询工具。
# 定义 TravelOrderReadTools，支持订单、状态和日期范围过滤。
from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from travel_agent_agent.agents.itinerary_manage.client import TravelManageApiClient


class TravelOrderReadTools:
    """封装当前用户可见的差旅单查询能力。"""

    def __init__(self, client: TravelManageApiClient) -> None:
        """保存内部 API 客户端。"""
        self.client = client

    async def query_travel_order(
        self,
        order_id: str | None = None,
        status: str | None = None,
        departure_date_from: str | None = None,
        departure_date_to: str | None = None,
        return_date_from: str | None = None,
        return_date_to: str | None = None,
    ) -> dict[str, Any]:
        """按订单、状态和出返程日期范围查询差旅单。"""
        return await self.client.request(
            "GET",
            "/internal/v1/travel-orders",
            params={
                "order_id": order_id,
                "status": status,
                "departure_date_from": departure_date_from,
                "departure_date_to": departure_date_to,
                "return_date_from": return_date_from,
                "return_date_to": return_date_to,
            },
        )

    def as_tools(self) -> list[StructuredTool]:
        """仅返回 query_travel_order 一个 LangChain 工具。"""
        return [
            StructuredTool.from_function(
                coroutine=self.query_travel_order,
                name="query_travel_order",
                description="只读查询当前用户差旅单，可按订单、状态和日期范围过滤。",
            )
        ]
