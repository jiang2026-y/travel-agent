# 本文件验证 MasterAgent 的自然语言意图简报、子 Agent 懒加载和未启用能力边界。
# 定义 FakeSubAgent 及序列化、注册、懒加载和会话透传测试。
import pytest

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.agents.master.agent import serialize_intent_result
from travel_agent_agent.agents.master.provider import (
    SubAgentConfig,
    SubAgentProvider,
    SubAgentRequest,
    SubAgentResult,
)
from travel_agent_agent.intent.result import IntentRecognitionResult


def _intent_result() -> IntentRecognitionResult:
    """构造用于简报测试的合法单意图结果。"""
    return IntentRecognitionResult.model_validate({
        "intents": [{
            "intent": "travel_order_query",
            "target_agent": "itineraryManageAgent",
            "confidence": "high",
            "reason": "用户查询已有差旅单。",
        }],
        "primary_intent": "travel_order_query",
        "multi_intent": False,
        "overall_reason": "当前请求是查询已有差旅单。",
        "source": "VECTOR",
        "trace_id": "trace-master-test",
    })


@pytest.mark.asyncio
async def test_intent_result_is_serialized_as_natural_language() -> None:
    """意图结果简报应面向 Master 使用自然语言，不泄露内部工具字段。"""
    message = serialize_intent_result(_intent_result())

    assert "travel_order_query" in message
    assert "VECTOR" in message
    assert "itineraryManageAgent" not in message


@pytest.mark.asyncio
async def test_sub_agent_provider_is_lazy_and_reuses_instance() -> None:
    """Provider 注册时不创建实例，首次调用创建一次并复用。"""
    created = 0

    def factory():
        nonlocal created
        created += 1

        async def handler(request: SubAgentRequest) -> SubAgentResult:
            return SubAgentResult(request.message, request.session_id)

        return handler

    provider = SubAgentProvider((SubAgentConfig("itinerary_manage_agent", factory),))
    assert provider.loaded_keys == ()
    request = SubAgentRequest("查询审批", None, AgentContext(trace_id="trace"))

    first = await provider.invoke("itinerary_manage_agent", request)
    second = await provider.invoke(
        "itinerary_manage_agent", SubAgentRequest("继续查询", "session-1", request.context)
    )

    assert created == 1
    assert provider.loaded_keys == ("itinerary_manage_agent",)
    assert first.session_id is None
    assert second.session_id == "session-1"


@pytest.mark.asyncio
async def test_unregistered_sub_agent_is_disabled_without_factory_call() -> None:
    """未注册或未启用 Agent 只能返回禁用提示，不能动态创建。"""
    provider = SubAgentProvider(())
    result = await provider.invoke(
        "booking_agent",
        SubAgentRequest("预订", None, AgentContext(trace_id="trace")),
    )

    assert "暂未启用" in result.content
