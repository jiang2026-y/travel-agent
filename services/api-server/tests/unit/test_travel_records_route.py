# 文件职责：验证“我的差旅”只读接口按当前用户返回差旅单及其审批状态。
# 定义 _RouteService 替身与两个用例：附带审批实例、无审批实例时降级为 None。
from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from travel_agent_api.api.routes.travel_records import list_travel_orders
from travel_agent_api.application.auth_service import AuthenticatedUser
from travel_agent_api.persistence.models import ApprovalRecord, TravelOrder


class _RouteService:
    """提供差旅单与审批实例的内存替身，记录批量审批查询的入参。"""

    def __init__(
        self, orders: list[TravelOrder], approvals: dict[str, ApprovalRecord] | None = None
    ) -> None:
        """保存固定返回数据与审批查询记录。"""
        self.orders = orders
        self.approvals = approvals or {}
        self.approval_queries: list[tuple[str, list[str | None]]] = []

    async def list_orders(self, user_id: str, status: str | None = None) -> list[TravelOrder]:
        """只返回传入用户自己的差旅单，模拟用户归属过滤。"""
        del status
        return [item for item in self.orders if item.user_id == user_id]

    async def list_approvals_by_process_instance_ids(
        self, user_id: str, process_instance_ids: Any
    ) -> dict[str, ApprovalRecord]:
        """记录批量查询入参并返回已配置的审批实例。"""
        self.approval_queries.append((user_id, list(process_instance_ids)))
        keys = set(process_instance_ids)
        return {key: value for key, value in self.approvals.items() if key in keys}


def _order(
    order_id: str, *, approval_id: str | None = None, user_id: str = "user_1"
) -> TravelOrder:
    """构造一条固定差旅单。"""
    return TravelOrder(
        order_id=order_id,
        user_id=user_id,
        destination="杭州",
        departure_city="北京",
        departure_date=date(2026, 9, 18),
        return_date=date(2026, 9, 20),
        purpose="参加客户拜访",
        status="SUBMITTED",
        approval_id=approval_id,
    )


def _request(service: _RouteService) -> Any:
    """构造仅暴露路由依赖字段的替身请求。"""
    state = SimpleNamespace(travel_order_service=service)
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _user(user_id: str = "user_1") -> AuthenticatedUser:
    """构造固定登录用户。"""
    return AuthenticatedUser(user_id=user_id, account="traveler", role="user")


@pytest.mark.asyncio
async def test_list_travel_orders_attaches_approval_status() -> None:
    """有审批实例时附带审批单号、审批状态与提交时间。"""
    submitted = datetime(2026, 9, 17, 7, 57, 10, tzinfo=UTC)
    service = _RouteService(
        [_order("order_1", approval_id="approval_1")],
        {
            "approval_1": ApprovalRecord(
                process_instance_id="approval_1",
                user_id="user_1",
                status="PENDING",
                order_id="order_1",
                submit_time=submitted,
            )
        },
    )

    payload = await list_travel_orders(_request(service), _user())

    assert len(payload["orders"]) == 1
    item = payload["orders"][0]
    assert item["order_id"] == "order_1"
    assert item["approval_id"] == "approval_1"
    assert item["approval_status"] == "PENDING"
    assert item["submitted_at"] == submitted.isoformat()
    assert service.approval_queries == [("user_1", ["approval_1"])]


@pytest.mark.asyncio
async def test_list_travel_orders_degrades_without_approval() -> None:
    """未关联审批实例时不报错，审批字段降级为空并跳过批量查询。"""
    service = _RouteService([_order("order_2")])

    payload = await list_travel_orders(_request(service), _user())

    item = payload["orders"][0]
    assert item["approval_id"] is None
    assert item["approval_status"] is None
    assert item["submitted_at"] is None
    assert service.approval_queries == [("user_1", [None])]


@pytest.mark.asyncio
async def test_list_travel_orders_hides_other_users_orders() -> None:
    """只返回当前用户自己的差旅单，不泄露他人申请。"""
    service = _RouteService([_order("order_3"), _order("order_4", user_id="user_2")])

    payload = await list_travel_orders(_request(service), _user())

    assert [item["order_id"] for item in payload["orders"]] == ["order_3"]
