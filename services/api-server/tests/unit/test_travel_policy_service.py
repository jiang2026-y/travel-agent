# 文件职责：验证差旅政策服务的职级解析、政策记录序列化和等级规则。
# 定义 parse_level、level_group、policy_record 的单元测试。
from __future__ import annotations

from decimal import Decimal

import pytest

from travel_agent_api.application.travel_policy_service import (
    level_group,
    parse_level,
    policy_record,
)
from travel_agent_api.persistence.models import TravelPolicyRule


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("P1", 1), ("p10", 10), ("P11+", 11), ("P11及以上", 11), ("8", 8)],
)
def test_parse_level_accepts_policy_formats(raw: str, expected: int) -> None:
    assert parse_level(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "管理岗", "P0", "P-1"])
def test_parse_level_rejects_invalid_values(raw: str | None) -> None:
    assert parse_level(raw) is None


def test_policy_record_hides_identity_and_serializes_limits() -> None:
    rule = TravelPolicyRule(
        level_min=1,
        level_max=8,
        city_tier="新一线",
        flight_class="ECONOMY",
        train_seat_class="SECOND",
        hotel_limit=Decimal("400.00"),
        hotel_star_limit=4,
        daily_meal_limit=Decimal("200.00"),
        daily_transport_limit=Decimal("200.00"),
        approval_threshold=Decimal("8000.00"),
        advance_booking_days=3,
    )

    result = policy_record(rule, 8, "新一线")

    assert result["level_group"] == "P1-P8"
    assert result["hotel_limit"] == 400.0
    assert "user_id" not in result
    assert "sensitive_ciphertext" not in result


def test_level_group_matches_document() -> None:
    assert level_group(8) == "P1-P8"
    assert level_group(10) == "P9-P10"
    assert level_group(11) == "P11+"
