# 本文件验证行程管理提示词片段、动态时间 Hook 和相对日期规范化规则。
# 定义提示词 include、动态 SYSTEM 消息和 Asia/Shanghai 日期解析测试。
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware import ModelRequest
from langchain.messages import HumanMessage, SystemMessage

from travel_agent_agent.agents.itinerary_manage.date_normalizer import (
    TravelDateError,
    TravelDateNormalizer,
)
from travel_agent_agent.agents.itinerary_manage.time_hook import DynamicTimeInjectionHook
from travel_agent_agent.core.prompt_loader import load_prompt


def test_manage_prompt_expands_time_rules() -> None:
    """行程管理静态提示词应展开 time-rules.md，而不是保留 include 占位符。"""
    prompt = load_prompt("prompts/itinerary-manage-agent-system.md")

    assert "时间处理规则" in prompt
    assert "{{include:" not in prompt


@pytest.mark.parametrize("path", ["../time-rules.md", "prompts/../time-rules.md", "prompts/x.txt"])
def test_prompt_loader_rejects_invalid_paths(path: str) -> None:
    """提示词加载器应拒绝路径穿越和非 Markdown 文件。"""
    with pytest.raises(ValueError, match="prompt_path_invalid"):
        load_prompt(path)


def test_dynamic_time_hook_injects_a_single_second_system_message() -> None:
    """动态时间应位于静态系统提示词之后、用户消息之前且使用固定消息 ID。"""
    hook = DynamicTimeInjectionHook(lambda: datetime(2026, 9, 3, 9, 0))
    request = ModelRequest(
        model=MagicMock(),
        system_message=SystemMessage(content="静态提示词", id="static"),
        messages=[
            SystemMessage(content="旧时间", id="dynamic_time"),
            HumanMessage(content="用户消息"),
        ],
    )

    updated = hook._with_dynamic_time(request)

    assert updated.system_message.content == "静态提示词"
    assert len([item for item in updated.messages if item.id == "dynamic_time"]) == 1
    assert updated.messages[0].id == "dynamic_time"
    assert updated.messages[1].content == "用户消息"
    assert "2026-09-03（星期四）" in updated.messages[0].content


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("明天", date(2026, 9, 4)),
        ("后天", date(2026, 9, 5)),
        ("下周一", date(2026, 9, 7)),
        ("下周四", date(2026, 9, 10)),
        ("月底", date(2026, 9, 30)),
        ("1号", date(2026, 10, 1)),
    ],
)
def test_date_normalizer_uses_china_business_calendar(expression: str, expected: date) -> None:
    """相对日期应按 Asia/Shanghai 下的已确认业务日历规范化。"""
    normalizer = TravelDateNormalizer(lambda: datetime(2026, 9, 3, 9, 0))

    assert normalizer.normalize(expression) == expected


def test_date_normalizer_rejects_invalid_range() -> None:
    """返回日期早于出发日期必须由服务端拒绝。"""
    normalizer = TravelDateNormalizer(lambda: datetime(2026, 9, 3, 9, 0))

    with pytest.raises(TravelDateError, match="return_before_departure"):
        normalizer.normalize_range("2026-09-10", "2026-09-09")
