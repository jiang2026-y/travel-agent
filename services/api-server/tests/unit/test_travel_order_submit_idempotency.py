# 文件职责：验证差旅申请提交的要素级幂等判定与事务写入行为。
# 定义内存会话替身，以及同行程去重、要素缺失跳过去重、异用户同号拒绝测试。
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, cast

import pytest

from travel_agent_api.application.travel_order_service import (
    SubmitOutcome,
    TravelOrderPersistenceService,
    TravelOrderStatus,
)
from travel_agent_api.persistence.models import ApprovalRecord, TravelOrder


class _FakeSession:
    """记录查询语句与写入行的内存会话替身。"""

    def __init__(
        self, existing: TravelOrder | None = None, duplicate: TravelOrder | None = None
    ) -> None:
        self.existing = existing
        self.duplicate = duplicate
        self.statements: list[Any] = []
        self.added: list[Any] = []
        self.committed = False

    async def get(self, model: Any, key: Any) -> TravelOrder | None:
        """按主键返回预置的既有差旅单。"""
        del model, key
        return self.existing

    async def scalar(self, statement: Any) -> TravelOrder | None:
        """记录去重查询语句并返回预置的重复差旅单。"""
        self.statements.append(statement)
        return self.duplicate

    def add_all(self, rows: list[Any]) -> None:
        """记录本次事务新增的行。"""
        self.added.extend(rows)

    async def commit(self) -> None:
        """标记事务已提交。"""
        self.committed = True

    async def refresh(self, instance: Any) -> None:
        """内存替身无需刷新实例。"""
        del instance


class _FakeSessionFactory:
    """返回同一内存会话的异步上下文管理器。"""

    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    def __call__(self) -> _FakeSessionFactory:
        """异步 Session 工厂的调用入口，返回自身作为上下文管理器。"""
        return self

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, *exc: object) -> bool:
        del exc
        return False


def _order(order_id: str, user_id: str = "user_1", **overrides: Any) -> TravelOrder:
    """构造一条差旅单事实。"""
    fields: dict[str, Any] = {
        "destination": "杭州",
        "departure_city": "北京",
        "departure_date": date(2026, 9, 18),
        "return_date": date(2026, 9, 20),
        "purpose": "客户拜访",
        "status": TravelOrderStatus.SUBMITTED,
    }
    fields.update(overrides)
    return TravelOrder(order_id=order_id, user_id=user_id, **fields)


_PAYLOAD: dict[str, Any] = {
    "destination": "杭州",
    "departure_city": "北京",
    "departure_date": date(2026, 9, 18),
    "return_date": date(2026, 9, 20),
    "purpose": "客户拜访",
}


def _service(session: _FakeSession) -> TravelOrderPersistenceService:
    """构造使用内存会话替身的差旅持久化服务。"""
    return TravelOrderPersistenceService(cast(Any, _FakeSessionFactory(session)))


def _submit(session: _FakeSession, idempotency_key: str = "order_new") -> SubmitOutcome:
    """以固定用户提交一份差旅申请。"""
    return asyncio.run(
        _service(session).submit(
            user_id="user_1", payload=dict(_PAYLOAD), idempotency_key=idempotency_key
        )
    )


def test_submit_returns_existing_order_for_same_trip_elements() -> None:
    """相同出行要素且状态生效时直接返回已有单，不写入任何新记录。"""
    duplicate = _order("order_existing")
    session = _FakeSession(duplicate=duplicate)

    outcome = _submit(session)

    assert outcome.duplicate is True
    assert outcome.order is duplicate
    assert session.added == []
    assert session.committed is False
    assert len(session.statements) == 1
    statement = str(session.statements[0])
    for column in (
        "travel_order.user_id",
        "travel_order.status IN",
        "travel_order.destination",
        "travel_order.departure_city",
        "travel_order.departure_date",
        "travel_order.return_date",
        "travel_order.purpose",
    ):
        assert column in statement


def test_submit_creates_order_and_approval_when_no_duplicate() -> None:
    """无同行程差旅单时创建差旅单与审批单，并以 SUBMITTED 状态返回。"""
    session = _FakeSession()

    outcome = _submit(session)

    assert outcome.duplicate is False
    assert session.committed is True
    assert [type(row) for row in session.added] == [TravelOrder, ApprovalRecord]
    order, approval = session.added
    assert outcome.order is order
    assert order.status == TravelOrderStatus.SUBMITTED
    assert order.approval_id == approval.process_instance_id
    assert approval.order_id == order.order_id


def test_missing_trip_element_skips_duplicate_lookup() -> None:
    """任一出行要素缺失时不做去重，按普通新建处理。"""
    session = _FakeSession(duplicate=_order("order_existing"))
    payload = dict(_PAYLOAD, purpose=None)

    outcome = asyncio.run(
        _service(session).submit(
            user_id="user_1", payload=payload, idempotency_key="order_new"
        )
    )

    assert outcome.duplicate is False
    assert session.statements == []
    assert session.committed is True


def test_same_order_id_owned_by_other_user_is_rejected() -> None:
    """同号重试必须校验归属，跨用户复用单号一律拒绝。"""
    session = _FakeSession(existing=_order("order_new", user_id="user_2"))

    with pytest.raises(PermissionError):
        _submit(session)
