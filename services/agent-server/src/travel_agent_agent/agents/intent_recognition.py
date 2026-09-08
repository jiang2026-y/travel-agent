# 本文件实现完整的 L0～L3 意图识别 Agent，不包含 ReAct 工具循环。
# 定义 IntentModelClient、IntentRecognitionAgent，负责规则、向量、改写和 L3 统一编排。
from __future__ import annotations

from typing import Protocol

from travel_agent_agent.agents.base import AgentBase, AgentContext
from travel_agent_agent.core.json_utils import parse_model_json
from travel_agent_agent.intent.l0_guard import StrongConjunctionGuard
from travel_agent_agent.intent.l1_rules import IntentRuleMatcher
from travel_agent_agent.intent.l2_vector import L2VectorMatcher
from travel_agent_agent.intent.recognizer import IntentRecognizer, RewritePort
from travel_agent_agent.intent.result import IntentRecognitionResult, IntentSource, L3ModelResponse


class IntentModelClient(Protocol):
    """定义 L3 模型客户端的最小兼容接口。"""

    async def classify(self, rewritten_question: str, history: str) -> str: ...


class IntentRecognitionAgent(AgentBase[str, IntentRecognitionResult]):
    """执行 L0→L1→L2→一次改写→L3 的完整意图识别流程。"""

    name = "intentRecognitionAgent"

    def __init__(
        self,
        client: IntentModelClient | None = None,
        *,
        l1: IntentRuleMatcher | None = None,
        l2: L2VectorMatcher | None = None,
        rewrite: RewritePort | None = None,
        l0: StrongConjunctionGuard | None = None,
    ) -> None:
        """注入规则、向量、改写和 L3 端口；所有阶段由本 Agent 统一调度。"""
        self._client = client
        self._pipeline = IntentRecognizer(
            l1 or IntentRuleMatcher.from_yaml(), l2=l2, l3=client, rewrite=rewrite, l0=l0
        )

    async def _execute(self, value: str, context: AgentContext) -> IntentRecognitionResult:
        """执行完整识别流程并返回统一结果。"""
        result, _ = await self._pipeline.recognize_with_rewrite(
            value, context.trace_id, context.context_summary
        )
        return result

    async def execute_with_rewrite(
        self, value: str, context: AgentContext
    ) -> tuple[IntentRecognitionResult, str]:
        """执行完整识别并返回改写后的问题，供 MasterAgent 作为 SYSTEM 消息使用。"""
        return await self._pipeline.recognize_with_rewrite(
            value, context.trace_id, context.context_summary
        )

    async def classify(self, rewritten_question: str, history: str) -> str:
        """兼容旧 L3Port；仅用于内部适配，不改变完整 Agent 主入口。"""
        if self._client is None:
            raise ValueError("intent_model_client_required")
        raw = await self._client.classify(rewritten_question, history)
        parsed = L3ModelResponse.model_validate(parse_model_json(raw))
        return IntentRecognitionResult(
            **parsed.model_dump(), source=IntentSource.LLM, trace_id="agent-local"
        ).model_dump_json()

    @property
    def pipeline(self) -> IntentRecognizer:
        """返回内部兼容管线，供运行时和旧测试访问。"""
        return self._pipeline
