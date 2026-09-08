# 本文件装配意图识别运行时依赖并明确真实 Provider 未配置时的拒绝边界。
# 定义 IntentRuntimeUnavailable、IntentRuntime、create_intent_runtime，分别表示不可用错误、
# 已装配识别器和基于独立 embedding/LLM Provider 初始化 1024 维向量库的工厂函数。
from __future__ import annotations

from dataclasses import dataclass

from travel_agent_agent.agents.intent_recognition import IntentRecognitionAgent
from travel_agent_agent.agents.query_rewrite import QueryRewriteAgent
from travel_agent_agent.intent.l1_rules import IntentRuleMatcher
from travel_agent_agent.intent.l2_vector import (
    EmbeddingPort,
    InMemoryIntentKnowledge,
    L2VectorMatcher,
    load_intent_seeds,
)
from travel_agent_agent.intent.recognizer import IntentRecognizer, L3Port, RewritePort


class IntentRuntimeUnavailable(RuntimeError):
    """表示真实 L2/L3 Provider 未完成登记或启动灌库，不能开始意图识别。"""


@dataclass(frozen=True, slots=True)
class IntentRuntime:
    """保存已初始化的统一识别器及其独立 L2 知识库。"""

    intent_agent: IntentRecognitionAgent
    knowledge: InMemoryIntentKnowledge

    @property
    def recognizer(self) -> IntentRecognizer:
        """兼容旧调用方，返回完整意图 Agent 的内部识别组件。"""
        return self.intent_agent.pipeline


async def create_intent_runtime(
    embedding: EmbeddingPort,
    l3: L3Port,
    rewrite: RewritePort | None = None,
) -> IntentRuntime:
    """在服务接收请求前灌入全部种子；任一 embedding 失败即拒绝启动。"""
    knowledge = InMemoryIntentKnowledge(dimension=1024)
    try:
        await knowledge.initialize(load_intent_seeds(), embedding)
    except Exception as error:
        raise IntentRuntimeUnavailable("intent_seed_initialization_failed") from error
    return IntentRuntime(
        intent_agent=IntentRecognitionAgent(
            l3,
            l1=IntentRuleMatcher.from_yaml(),
            l2=L2VectorMatcher(knowledge, embedding),
            rewrite=QueryRewriteAgent(rewrite) if rewrite is not None else None,
        ),
        knowledge=knowledge,
    )
