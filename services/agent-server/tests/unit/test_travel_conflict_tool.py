# 本文件验证行程冲突工具的日期规范化和结构化事实结果。
# 定义伪内部客户端及无效日期、结构化 HIGH 结果测试。
from __future__ import annotations

import pytest

from travel_agent_agent.agents.itinerary_manage.schemas import TravelOrderConflictRequest
from travel_agent_agent.agents.itinerary_manage.tools import TravelOrderConflictTools


class _ConflictClient:
    """返回固定冲突结果的内部 API 客户端替身。"""

    async def request(self, *_: object, **__: object) -> dict[str, object]:
        """提供满足 Pydantic Record 的固定 HIGH 事实。"""
        return {
            "valid": True,
            "has_conflict": True,
            "severity": "HIGH",
            "summary": "检测到跨城时间重叠",
            "conflicts": [
                {
                    "type": "cross_city_overlap",
                    "severity": "HIGH",
                    "description": "跨城时间重叠。",
                    "suggestion": "调整日期。",
                }
            ],
        }


@pytest.mark.asyncio
async def test_conflict_tool_returns_structured_record() -> None:
    """工具应将 API JSON 转换为结构化冲突 Record。"""
    tool = TravelOrderConflictTools(_ConflictClient())  # type: ignore[arg-type]

    result = await tool.check_travel_order_conflicts(
        TravelOrderConflictRequest(
            departure_city="北京",
            destination="上海",
            departure_date="2026-09-10",
            return_date="2026-09-12",
        )
    )

    assert result.has_conflict is True
    assert result.severity == "HIGH"
    assert result.conflicts[0].description == "跨城时间重叠。"


@pytest.mark.asyncio
async def test_conflict_tool_returns_invalid_record_for_unparseable_date() -> None:
    """日期无法规范化时应返回 valid=false，而不是把冲突当作异常抛出。"""
    tool = TravelOrderConflictTools(_ConflictClient())  # type: ignore[arg-type]

    result = await tool.check_travel_order_conflicts(
        TravelOrderConflictRequest(
            departure_city="北京",
            destination="上海",
            departure_date="不存在的日期",
            return_date="2026-09-12",
        )
    )

    assert result.valid is False
    assert result.has_conflict is False
