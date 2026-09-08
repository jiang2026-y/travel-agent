# 本文件验证差旅申请单和审批单状态机及返程日期自动完成判定。
# 定义状态转移和自动完成边界测试函数。
from datetime import date

import pytest

from travel_agent_api.application.travel_order_service import (
    TravelOrderStatus,
    should_auto_complete,
    transition_travel_order,
)


def test_draft_can_be_cancelled_and_submitted_can_be_cancelled() -> None:
    """草稿和审批中的差旅单均允许取消。"""
    assert transition_travel_order(TravelOrderStatus.DRAFT, TravelOrderStatus.CANCELLED)
    assert transition_travel_order(TravelOrderStatus.SUBMITTED, TravelOrderStatus.CANCELLED)


def test_rejected_is_terminal() -> None:
    """拒绝状态不可恢复、修改或完成。"""
    with pytest.raises(ValueError, match="transition_not_allowed"):
        transition_travel_order(TravelOrderStatus.REJECTED, TravelOrderStatus.SUBMITTED)


def test_approved_auto_completes_the_day_after_return_date() -> None:
    """返程日当天保持通过，次日才自动完成。"""
    return_date = date(2026, 9, 4)

    assert should_auto_complete(TravelOrderStatus.APPROVED, return_date, date(2026, 9, 4)) is False
    assert should_auto_complete(TravelOrderStatus.APPROVED, return_date, date(2026, 9, 5)) is True
