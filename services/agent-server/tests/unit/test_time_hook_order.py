# 本文件验证动态中国时间消息位于静态系统提示词之后且每轮只保留一条。
# 定义固定时钟与模型请求顺序测试。
from datetime import datetime

from langchain.agents.middleware import ModelRequest
from langchain.messages import HumanMessage, SystemMessage

from travel_agent_agent.agents.itinerary_manage.time_hook import DynamicTimeInjectionHook


def test_dynamic_time_follows_static_system_message() -> None:
    """动态时间应排在静态系统提示词后、用户消息前。"""
    hook = DynamicTimeInjectionHook(lambda: datetime(2026, 9, 3, 8, 0))
    request = ModelRequest(
        model=None,  # type: ignore[arg-type]
        system_message=SystemMessage(content="静态规则", id="static"),
        messages=[
            SystemMessage(content="静态规则", id="static"),
            HumanMessage(content="明天出差"),
        ],
    )
    updated = hook._with_dynamic_time(request)
    assert isinstance(updated.messages[0], SystemMessage)
    assert updated.messages[0].id == "static"
    assert updated.messages[1].id == "dynamic_time"
    assert isinstance(updated.messages[2], HumanMessage)
