# 本文件验证差旅冲突检测仅返回事实和建议，不直接阻断业务流程。
# 定义冲突服务替身、evaluate_travel_conflicts 的规则测试（重叠分级、同日交接、
# 正反向次日衔接、日期倒置与严重等级排序）及自身排除测试。
from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest

from travel_agent_api.application.travel_order_service import (
    TravelOrderFact,
    TravelOrderPersistenceService,
    evaluate_travel_conflicts,
)
from travel_agent_api.config.city_tier import load_city_tier_config


class _ConflictService(TravelOrderPersistenceService):
    """提供可控冲突数据的领域服务替身。"""

    def __init__(self, conflicts: list[dict[str, object]]) -> None:
        self.conflicts = conflicts

    async def detect_conflicts(self, **_: object) -> list[dict[str, object]]:
        """返回预设冲突，隔离数据库依赖。"""
        return self.conflicts


@pytest.mark.asyncio
async def test_check_conflicts_returns_high_fact_without_blocking() -> None:
    """HIGH 冲突应正常返回事实、建议和 has_conflict，而非抛出异常。"""
    service = _ConflictService(
        [
            {
                "type": "cross_city_overlap",
                "severity": "HIGH",
                "description": "跨城时间重叠。",
                "suggestion": "调整日期。",
            }
        ]
    )

    result = await service.check_conflicts(
        user_id="user_001",
        departure_city="北京",
        destination="上海",
        departure_date=date(2026, 9, 10),
        return_date=date(2026, 9, 12),
    )

    assert result["valid"] is True
    assert result["has_conflict"] is True
    assert result["severity"] == "HIGH"
    assert result["conflicts"][0]["suggestion"] == "调整日期。"


@pytest.mark.asyncio
async def test_check_conflicts_returns_none_when_no_fact_exists() -> None:
    """无冲突时应返回 NONE 和空列表。"""
    service = _ConflictService([])

    result = await service.check_conflicts(
        user_id="user_001",
        departure_city="北京",
        destination="上海",
        departure_date=date(2026, 9, 10),
        return_date=date(2026, 9, 12),
        exclude_order_id="order_self",
    )

    assert result == {
        "valid": True,
        "has_conflict": False,
        "severity": "NONE",
        "summary": "未发现与已有差旅冲突",
        "conflicts": [],
    }


def test_city_tier_config_is_available_to_travel_order_service() -> None:
    service = TravelOrderPersistenceService.__new__(TravelOrderPersistenceService)
    service.city_tier_config = load_city_tier_config()

    assert service.city_tier_config.transit_minutes("北京", "上海") == 300


def _fact(
    order_id: str,
    departure_city: str,
    destination: str,
    departure_date: date,
    return_date: date,
    status: str = "SUBMITTED",
) -> TravelOrderFact:
    """构造一条已有行程事实。"""
    return TravelOrderFact(
        order_id=order_id,
        departure_city=departure_city,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        status=status,
    )


def _transit(minutes: int) -> Callable[[str | None, str | None], int]:
    """返回固定跨城衔接时长的替身计量函数。"""

    def _estimate(_: str | None, __: str | None) -> int:
        return minutes

    return _estimate


def test_overlap_with_different_destination_is_high() -> None:
    """仅出发地相同、目的地不同的时间重叠必须判为 HIGH。

    回归场景：已有 北京→杭州（09-18~09-20），本次 北京→南京（09-19~09-24）。
    旧实现按“共享北京”误判为 LOW 同城重复提交，导致模型默默跳过冲突提示。
    """
    conflicts = evaluate_travel_conflicts(
        [_fact("order_a", "北京", "杭州", date(2026, 9, 18), date(2026, 9, 20))],
        departure_city="北京",
        destination="南京",
        departure_date=date(2026, 9, 19),
        return_date=date(2026, 9, 24),
        transit_minutes=_transit(300),
    )

    assert [item["severity"] for item in conflicts] == ["HIGH"]
    assert conflicts[0]["type"] == "cross_city_overlap"
    assert conflicts[0]["order_id"] == "order_a"
    assert "北京 → 杭州" in str(conflicts[0]["order_summary"])


def test_overlap_with_same_route_is_low() -> None:
    """出发地与目的地都相同的重叠视为同城重复提交。"""
    conflicts = evaluate_travel_conflicts(
        [_fact("order_b", "北京", "杭州", date(2026, 9, 18), date(2026, 9, 20))],
        departure_city="北京",
        destination="杭州",
        departure_date=date(2026, 9, 19),
        return_date=date(2026, 9, 22),
        transit_minutes=_transit(300),
    )

    assert [item["severity"] for item in conflicts] == ["LOW"]
    assert conflicts[0]["type"] == "same_city_overlap"


def test_reversed_overlap_adds_round_trip_hint() -> None:
    """出发地与目的地互换的重叠给出往返拆单提示。"""
    conflicts = evaluate_travel_conflicts(
        [_fact("order_c", "杭州", "北京", date(2026, 9, 18), date(2026, 9, 20))],
        departure_city="北京",
        destination="杭州",
        departure_date=date(2026, 9, 19),
        return_date=date(2026, 9, 22),
        transit_minutes=_transit(300),
    )

    assert "往返拆单" in str(conflicts[0]["description"])


def test_same_day_handoff_below_eight_hours_is_medium() -> None:
    """同日跨城交接 5 小时视为衔接偏紧，判为 MEDIUM。"""
    conflicts = evaluate_travel_conflicts(
        [_fact("order_d", "北京", "杭州", date(2026, 9, 18), date(2026, 9, 20))],
        departure_city="上海",
        destination="南京",
        departure_date=date(2026, 9, 20),
        return_date=date(2026, 9, 22),
        transit_minutes=_transit(300),
    )

    assert [item["severity"] for item in conflicts] == ["MEDIUM"]
    assert conflicts[0]["type"] == "same_day_transit_insufficient"


def test_same_day_handoff_over_twenty_four_hours_is_high() -> None:
    """同日跨城交接超过 24 小时视为当日无法完成。"""
    conflicts = evaluate_travel_conflicts(
        [_fact("order_e", "北京", "杭州", date(2026, 9, 18), date(2026, 9, 20))],
        departure_city="上海",
        destination="南京",
        departure_date=date(2026, 9, 20),
        return_date=date(2026, 9, 22),
        transit_minutes=_transit(30 * 60),
    )

    assert [item["severity"] for item in conflicts] == ["HIGH"]


def test_same_city_handoff_is_not_a_conflict() -> None:
    """同城首尾相接不需要跨城交通，不产生冲突。"""
    conflicts = evaluate_travel_conflicts(
        [_fact("order_f", "北京", "杭州", date(2026, 9, 18), date(2026, 9, 20))],
        departure_city="杭州",
        destination="上海",
        departure_date=date(2026, 9, 20),
        return_date=date(2026, 9, 22),
        transit_minutes=_transit(300),
    )

    assert conflicts == []


def test_adjacent_day_handoff_within_one_day_is_not_a_conflict() -> None:
    """相邻日跨城交通不超过 8 小时即认为一天足够，不告警。

    回归场景：已有 北京→南京（09-25~09-27），本次 北京→南京（09-19~09-24）。
    """
    conflicts = evaluate_travel_conflicts(
        [_fact("order_g", "北京", "南京", date(2026, 9, 25), date(2026, 9, 27))],
        departure_city="北京",
        destination="南京",
        departure_date=date(2026, 9, 19),
        return_date=date(2026, 9, 24),
        transit_minutes=_transit(300),
    )

    assert conflicts == []


def test_reverse_adjacent_day_handoff_flags_route_break() -> None:
    """候选行程结束次日已有行程出发且跨城过远时必须告警（旧实现缺失该方向）。"""
    conflicts = evaluate_travel_conflicts(
        [_fact("order_h", "北京", "南京", date(2026, 9, 25), date(2026, 9, 27))],
        departure_city="上海",
        destination="乌鲁木齐",
        departure_date=date(2026, 9, 19),
        return_date=date(2026, 9, 24),
        transit_minutes=_transit(900),
    )

    assert [item["severity"] for item in conflicts] == ["MEDIUM"]
    assert conflicts[0]["type"] == "next_day_route_break"
    assert "本次行程在 2026-09-24 结束于 乌鲁木齐" in str(conflicts[0]["description"])


def test_forward_adjacent_day_handoff_flags_route_break() -> None:
    """已有行程结束次日候选出发且跨城过远时报路径断裂。"""
    conflicts = evaluate_travel_conflicts(
        [_fact("order_i", "北京", "南京", date(2026, 9, 19), date(2026, 9, 24))],
        departure_city="上海",
        destination="成都",
        departure_date=date(2026, 9, 25),
        return_date=date(2026, 9, 27),
        transit_minutes=_transit(900),
    )

    assert [item["severity"] for item in conflicts] == ["MEDIUM"]
    assert "已有行程" in str(conflicts[0]["description"])


def test_conflicts_are_sorted_by_severity() -> None:
    """结果按严重等级降序排列，便于模型优先处理 HIGH。"""
    conflicts = evaluate_travel_conflicts(
        [
            _fact("order_j", "北京", "杭州", date(2026, 9, 18), date(2026, 9, 20)),
            _fact("order_k", "北京", "成都", date(2026, 9, 22), date(2026, 9, 23)),
        ],
        departure_city="北京",
        destination="南京",
        departure_date=date(2026, 9, 20),
        return_date=date(2026, 9, 24),
        transit_minutes=_transit(300),
    )

    assert [item["severity"] for item in conflicts] == ["HIGH", "MEDIUM"]
    assert [item["order_id"] for item in conflicts] == ["order_k", "order_j"]


@pytest.mark.asyncio
async def test_check_conflicts_rejects_inverted_dates() -> None:
    """出发日期晚于返回日期时返回 valid=false，而不是给出冲突结论。"""
    service = TravelOrderPersistenceService.__new__(TravelOrderPersistenceService)
    service.city_tier_config = load_city_tier_config()

    result = await service.check_conflicts(
        user_id="user_001",
        departure_city="北京",
        destination="上海",
        departure_date=date(2026, 9, 12),
        return_date=date(2026, 9, 10),
    )

    assert result["valid"] is False
    assert result["has_conflict"] is False
    assert result["severity"] == "NONE"
    assert "晚于返回日期" in str(result["summary"])
