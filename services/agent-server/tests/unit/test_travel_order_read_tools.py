# 文件职责：验证只读差旅单工具的参数映射与预订前审批、时间有效性校验。
# 定义审批校验、时间校验与列表过滤参数测试。
from __future__ import annotations

from typing import Any

import pytest

from travel_agent_agent.agents.common.travel_order_read_tools import TravelOrderReadTools


class _RecordingClient:
    """记录内部 API 请求的最小替身。"""

    def __init__(self, orders: list[dict[str, Any]] | None = None) -> None:
        self.orders = orders or []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """记录调用并返回预设差旅单列表。"""
        self.calls.append((method, path, kwargs))
        if path.endswith("/approvals"):
            return {
                "approvals": [
                    {"process_instance_id": "p-1", "order_id": "o-1", "status": "PENDING"}
                ]
            }
        return {"orders": self.orders}


def _order(**overrides: Any) -> dict[str, Any]:
    """构造一条差旅单事实。"""
    order = {
        "order_id": "o-1",
        "status": "APPROVED",
        "departure_city": "上海",
        "destination": "杭州",
        "departure_date": "2099-01-01",
        "return_date": "2099-01-03",
        "purpose": "客户拜访",
        "approval_id": "a-1",
        "user_id": "u-1",
    }
    order.update(overrides)
    return order


@pytest.mark.asyncio
async def test_query_travel_order_maps_trip_filters() -> None:
    """按行程要素查询必须把城市与日期映射为内部接口筛选参数。"""
    client = _RecordingClient([_order()])
    result = await TravelOrderReadTools(client).query_travel_order("上海", "杭州", "2099-01-01")
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("GET", "/internal/v1/travel-orders")
    assert kwargs["params"] == {
        "departure_city": "上海",
        "destination": "杭州",
        "departure_date_from": "2099-01-01",
        "departure_date_to": "2099-01-01",
    }
    assert result["found"] is True
    assert result["order_id"] == "o-1"
    assert "user_id" not in result


@pytest.mark.asyncio
async def test_query_travel_orders_maps_date_range() -> None:
    """列表查询必须把起止日期映射为出发日期区间。"""
    client = _RecordingClient([_order()])
    result = await TravelOrderReadTools(client).query_travel_orders(
        status="APPROVED", start_date="2099-01-01", end_date="2099-02-01"
    )
    _, _, kwargs = client.calls[0]
    assert kwargs["params"] == {
        "status": "APPROVED",
        "departure_date_from": "2099-01-01",
        "departure_date_to": "2099-02-01",
    }
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_check_travel_order_approval_rejects_unapproved_order() -> None:
    """未审批通过的差旅单必须拒绝预订，并返回可展示的原因。"""
    approved_client = _RecordingClient([_order()])
    approved = await TravelOrderReadTools(approved_client).check_travel_order_approval("o-1")
    assert approved["approved"] is True
    assert approved["destination"] == "杭州"

    pending_client = _RecordingClient([_order(status="SUBMITTED")])
    pending = await TravelOrderReadTools(pending_client).check_travel_order_approval("o-1")
    assert pending["approved"] is False
    assert "尚未完成审批" in pending["message"]


@pytest.mark.asyncio
async def test_check_travel_time_validity_blocks_started_trip() -> None:
    """已开始或已结束的行程必须被判为不可继续处理。"""
    past_client = _RecordingClient(
        [_order(departure_date="2020-01-01", return_date="2020-01-03")]
    )
    blocked = await TravelOrderReadTools(past_client).check_travel_time_validity("o-1")
    assert blocked["valid"] is False
    assert blocked["blocked_orders"][0]["order_id"] == "o-1"

    future_client = _RecordingClient([_order()])
    valid = await TravelOrderReadTools(future_client).check_travel_time_validity("o-1")
    assert valid["valid"] is True
