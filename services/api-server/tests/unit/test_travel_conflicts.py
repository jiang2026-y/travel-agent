# 本文件验证差旅冲突检测仅返回事实和建议，不直接阻断业务流程。
# 定义冲突服务替身及 HIGH、LOW、无冲突和自身排除测试。
from __future__ import annotations

from datetime import date

import pytest

from travel_agent_api.application.travel_order_service import TravelOrderPersistenceService


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
