# 文件职责：验证问题推荐旁路的关键词、JSON 解析和 Redis 事件幂等行为。
# 定义推荐旁路单元测试，确保失败隔离和事件格式符合 SSE 契约。
from __future__ import annotations

import pytest

from travel_agent_agent.recommendation.agent import (
    RecommendationError,
    parse_recommendation_questions,
)
from travel_agent_agent.recommendation.events import InMemoryRecommendationEventStore
from travel_agent_agent.recommendation.signals import continuation_signals


def test_continuation_signals_are_grouped_and_normalized() -> None:
    """快速操作词表应支持分组提示词和英文大小写标准化。"""
    assert continuation_signals.is_valid(" 确认 ")
    assert continuation_signals.is_valid("YES")
    assert continuation_signals.group_for("修改一下") == "修改/补充类"
    assert "取消/重新类" in continuation_signals.as_grouped_prompt()


def test_recommendation_json_is_limited_and_deduplicated() -> None:
    """推荐解析应去重、过滤空值并限制最多四条。"""
    content = '{"questions": ["确定", "", "确定", "修改", "继续", "补充", "取消"]}'
    assert parse_recommendation_questions(content) == ["确定", "修改", "继续", "补充"]


def test_recommendation_json_invalid_raises_stable_error() -> None:
    """非法模型输出应转换为稳定推荐错误。"""
    with pytest.raises(RecommendationError, match="recommendation_json_invalid"):
        parse_recommendation_questions("not-json")


@pytest.mark.asyncio
async def test_recommendation_events_are_idempotent() -> None:
    """同一 Run 和答案版本重复发布只能产生一条推荐事件。"""
    store = InMemoryRecommendationEventStore()
    first = await store.publish("run_1", "trace_1", "answer_1", ["确定"])
    second = await store.publish("run_1", "trace_1", "answer_1", ["修改"])
    assert first is not None
    assert second is None
    events = await store.list_events("run_1")
    assert events[0]["type"] == "recommendations"
    assert events[0]["data"] == {"items": ["确定"]}
