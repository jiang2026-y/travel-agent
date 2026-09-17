# 本文件验证非 ReAct AgentBase、意图识别 Agent 和问题改写 Agent 的兼容行为。
# 定义 FakeIntentClient、FakeRewriteClient 及三个 AgentBase 单次执行测试。
import json

import pytest

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.agents.intent_recognition import IntentRecognitionAgent
from travel_agent_agent.agents.query_rewrite import QueryRewriteAgent


class FakeIntentClient:
    """返回一个合法的单意图 JSON，隔离真实百炼调用。"""

    async def classify(self, rewritten_question: str, history: str) -> str:
        """返回合法的航班查询意图结果。"""
        return json.dumps({
            "intents": [{
                "intent": "flight_search",
                "target_agent": "bookingAgent",
                "confidence": "high",
                "reason": "用户查询航班信息。",
            }],
            "primary_intent": "flight_search",
            "multi_intent": False,
            "overall_reason": "当前诉求为航班查询。",
        }, ensure_ascii=False)


class FakeRewriteClient:
    """返回固定改写文本，隔离真实百炼调用。"""

    async def rewrite(self, text: str, history: str) -> str:
        """返回补全后的问题。"""
        return "请查询北京到上海的航班"


@pytest.mark.asyncio
async def test_intent_agent_returns_validated_result() -> None:
    """意图 Agent 应继承 AgentBase 并返回校验后的模型结果。"""
    agent = IntentRecognitionAgent(FakeIntentClient())
    result = await agent.run("查航班", AgentContext(trace_id="trace-test"))

    assert agent.name == "intentRecognitionAgent"
    assert result.primary_intent == "flight_search"


@pytest.mark.asyncio
async def test_intent_agent_keeps_legacy_classify_contract() -> None:
    """意图 Agent 应继续兼容现有 L3Port 的 classify 方法。"""
    result = json.loads(await IntentRecognitionAgent(FakeIntentClient()).classify("查航班", ""))

    assert result["primary_intent"] == "flight_search"


@pytest.mark.asyncio
async def test_query_rewrite_agent_returns_structured_result_and_legacy_text() -> None:
    """问题改写 Agent 应提供结构化 run 和旧协议 rewrite 两种入口。"""
    agent = QueryRewriteAgent(FakeRewriteClient())
    result = await agent.run("审批通过了，查一下", AgentContext(trace_id="trace-test"))

    assert agent.name == "queryRewriteAgent"
    assert result.changed is True
    assert await agent.rewrite("审批通过了，查一下", "") == result.rewritten_text
