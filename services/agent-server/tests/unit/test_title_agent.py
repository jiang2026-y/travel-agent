# 文件职责：验证会话标题生成的结果清洗与失败降级。
# 定义标题清洗、模型成功、模型异常与空输入测试。
from __future__ import annotations

from typing import Any

import pytest

from travel_agent_agent.title.agent import (
    MAX_TITLE_LENGTH,
    ConversationTitleAgent,
    TitleGenerationError,
    clean_title,
)


class _StubModel:
    """返回固定内容的聊天模型替身。"""

    def __init__(self, content: Any = "上海出差申请", error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls = 0

    async def ainvoke(self, messages: Any) -> Any:
        """记录调用次数并返回固定内容或抛出异常。"""
        del messages
        self.calls += 1
        if self.error is not None:
            raise self.error
        return type("Response", (), {"content": self.content})()


def test_clean_title_strips_quotes_markdown_and_truncates() -> None:
    """标题必须去掉引号与 Markdown 包裹，并截断到 24 字。"""
    assert clean_title('"上海出差申请"') == "上海出差申请"
    assert clean_title("```\n北京出差申请\n```") == "北京出差申请"
    assert clean_title("**杭州行程规划**") == "**杭州行程规划**"
    assert clean_title("") is None
    assert len(clean_title("申" * 40) or "") == MAX_TITLE_LENGTH


@pytest.mark.asyncio
async def test_title_agent_returns_cleaned_title() -> None:
    """正常返回时输出清洗后的标题。"""
    model = _StubModel('"查询北京到杭州航班"')
    agent = ConversationTitleAgent(model)
    title = await agent.generate(
        "帮我查一下北京到杭州的机票", '{"primary_intent": "flight_search"}'
    )
    assert title == "查询北京到杭州航班"
    assert model.calls == 1


@pytest.mark.asyncio
async def test_title_agent_skips_empty_question_and_wraps_errors() -> None:
    """空问题不入模型；模型异常转换为稳定错误类型。"""
    model = _StubModel()
    agent = ConversationTitleAgent(model)
    assert await agent.generate("   ") is None
    assert model.calls == 0

    failing = ConversationTitleAgent(_StubModel(error=RuntimeError("boom")))
    with pytest.raises(TitleGenerationError):
        await failing.generate("去上海出差")
