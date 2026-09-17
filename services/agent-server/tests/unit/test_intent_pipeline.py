# 本文件验证 L2 向量阈值/margin、种子加载、一次改写、L3 严格多意图与安全回退闭环。
# 定义 DeterministicEmbedder、StaticL3 与各层短路、向量歧义、改写、L3、失败回退测试函数。
from __future__ import annotations

import json

import pytest

from travel_agent_agent.core.json_utils import parse_model_json
from travel_agent_agent.intent.l1_rules import IntentRuleMatcher
from travel_agent_agent.intent.l2_vector import (
    InMemoryIntentKnowledge,
    IntentSeed,
    L2VectorMatcher,
    load_intent_seeds,
)
from travel_agent_agent.intent.recognizer import IntentRecognizer
from travel_agent_agent.intent.result import IntentSource
from travel_agent_agent.intent.runtime import create_intent_runtime


class DeterministicEmbedder:
    """测试用 embedding 替身，按文本返回预设 1024 维向量。"""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        """保存测试向量映射。"""
        self.vectors = vectors

    async def embed(self, text: str) -> list[float]:
        """返回预设向量，不进行任何外部请求。"""
        return self.vectors[text]


class BatchDeterministicEmbedder(DeterministicEmbedder):
    """测试用批量 embedding 替身，记录每次调用的文本批次。"""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        """初始化预设向量映射和批处理调用记录。"""
        super().__init__(vectors)
        self.batches: list[list[str]] = []

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """记录批次并按输入顺序返回预设向量。"""
        self.batches.append(texts)
        return [self.vectors[text] for text in texts]


class StaticL3:
    """测试用 L3 端口，返回固定严格 JSON。"""

    def __init__(self, value: str) -> None:
        """保存要返回的原始 JSON 文本。"""
        self.value = value

    async def classify(self, rewritten_question: str, history: str) -> str:
        """返回固定文本，验证编排器解析和校验逻辑。"""
        return self.value


class StaticRewrite:
    """测试用一次问题改写端口。"""

    async def rewrite(self, text: str, history: str) -> str:
        """将未命中口语改写为可由 L1 识别的表达。"""
        return "帮我查航班"


class CompoundRewrite:
    """测试用改写端口：保留规划与订酒店两个动作，供 L3 多意图识别。"""

    async def rewrite(self, text: str, history: str) -> str:
        """返回消除状态指代后的复合出行诉求。"""
        return "审批已通过，请规划杭州行程并预订酒店"


class UnchangedRewrite:
    """测试用改写端口：原样返回输入，验证仍会重新执行 L0/L1/L2。"""

    async def rewrite(self, text: str, history: str) -> str:
        """返回未变化文本。"""
        return text


def _vector(first: float, second: float = 0.0) -> list[float]:
    """创建用于余弦相似度断言的 1024 维测试向量。"""
    return [first, second] + [0.0] * 1022


@pytest.mark.asyncio
async def test_l2_loads_versioned_seed_corpus_and_returns_vector_hit() -> None:
    """种子库必须覆盖 16 类意图，并按 Top-1 阈值生成 VECTOR 结果。"""
    seeds = load_intent_seeds()
    assert {seed.intent_code for seed in seeds} == {
        "travel_application", "travel_cancel", "travel_modify", "approval_query",
        "travel_order_query", "itinerary_planning", "flight_search", "train_search",
        "hotel_search", "booking", "reimbursement", "policy_query", "attractions_query",
        "general_info", "greeting", "unknown",
    }

    knowledge = InMemoryIntentKnowledge()
    fixture_seeds = [
        IntentSeed("flight-001", "flight_search", "种子航班"),
        IntentSeed("flight-002", "flight_search", "种子航班二"),
    ]
    embedder = DeterministicEmbedder({
        "种子航班": _vector(1),
        "种子航班二": _vector(0.999, 0.01),
        "用户口语": _vector(1),
    })
    await knowledge.initialize(fixture_seeds, embedder)
    result = await L2VectorMatcher(knowledge, embedder).match("用户口语", "trace-l2-001")

    assert result is not None
    assert result.source is IntentSource.VECTOR
    assert result.primary_intent == "flight_search"
    assert result.sample_version == "intent-seed-v1"
    assert result.score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_l2_defers_when_different_intents_have_small_margin() -> None:
    """不同意图的 Top-1/Top-2 分差小于 0.05 时必须放行 L3。"""
    knowledge = InMemoryIntentKnowledge()
    seeds = [
        IntentSeed("flight-001", "flight_search", "种子航班"),
        IntentSeed("hotel-001", "hotel_search", "种子酒店"),
    ]
    embedder = DeterministicEmbedder({
        "种子航班": _vector(1),
        "种子酒店": _vector(0.99, 0.14),
        "用户口语": _vector(1),
    })
    await knowledge.initialize(seeds, embedder)

    assert await L2VectorMatcher(knowledge, embedder).match("用户口语", "trace-l2-002") is None


@pytest.mark.asyncio
async def test_l2_hits_same_intent_even_when_margin_is_large() -> None:
    """Top-2 必须为同一意图且与 Top-1 分差不大于 0.05，否则进入改写/L3。"""
    knowledge = InMemoryIntentKnowledge()
    seeds = [
        IntentSeed("flight-001", "flight_search", "种子航班"),
        IntentSeed("flight-002", "flight_search", "另一航班"),
    ]
    embedder = DeterministicEmbedder({
        "种子航班": _vector(1),
        "另一航班": _vector(0.7, 0.71),
        "用户口语": _vector(1),
    })
    await knowledge.initialize(seeds, embedder)

    result = await L2VectorMatcher(knowledge, embedder).match("用户口语", "trace-l2-003")
    assert result is not None
    assert result.primary_intent == "flight_search"


@pytest.mark.asyncio
async def test_pipeline_rewrites_once_before_l3() -> None:
    """L1/L2 未命中时只改写一次，改写后 L1 命中应跳过 L3。"""
    recognizer = IntentRecognizer(IntentRuleMatcher.from_yaml(), rewrite=StaticRewrite())

    result = await recognizer.recognize("一个含糊口语", "trace-pipeline-001")

    assert result.source is IntentSource.RULE
    assert result.primary_intent == "flight_search"


@pytest.mark.asyncio
async def test_unchanged_rewrite_still_rechecks_l0_l1_l2() -> None:
    """改写文本未变化但非空时，仍必须完成一次二次 L0/L1/L2 检查。"""
    recognizer = IntentRecognizer(
        IntentRuleMatcher.from_yaml(), rewrite=UnchangedRewrite(), l3=StaticL3("not json")
    )

    result = await recognizer.recognize("无法匹配的表达", "trace-pipeline-unchanged")

    assert result.diagnostics["rewrite_called"] is True
    assert result.diagnostics["rewrite_changed"] is False
    assert result.diagnostics["rewritten_l0"] == "single"
    assert result.diagnostics["rewritten_l1"] == "miss"
    assert result.diagnostics["rewritten_l2"] == "unavailable"


@pytest.mark.asyncio
async def test_compound_planning_and_hotel_booking_rewrites_then_uses_l3() -> None:
    """状态陈述加复合动作不得被 L1 短路，改写一次后应由 L3 输出多意图。"""
    l3 = StaticL3(json.dumps({
        "intents": [
            {
                "intent": "itinerary_planning",
                "target_agent": "itineraryPlanAgent",
                "confidence": "high",
                "reason": "用户希望先形成杭州出行计划。",
            },
            {
                "intent": "booking",
                "target_agent": "bookingAgent",
                "confidence": "medium",
                "reason": "用户同时提出酒店预订诉求。",
            },
        ],
        "primary_intent": "itinerary_planning",
        "multi_intent": True,
        "overall_reason": "用户同时提出行程规划和酒店预订，需要按顺序处理。",
    }, ensure_ascii=False))
    knowledge = InMemoryIntentKnowledge()
    seeds = [
        IntentSeed("plan-001", "itinerary_planning", "规划行程"),
        IntentSeed("booking-001", "booking", "预订酒店"),
    ]
    compound = "审批通过了，帮我规划下杭州行程订个酒店"
    rewritten = "审批已通过，请规划杭州行程并预订酒店"
    embedder = DeterministicEmbedder({
        "规划行程": _vector(1),
        "预订酒店": _vector(0.8, 0.6),
        compound: _vector(1),
        rewritten: _vector(1),
    })
    await knowledge.initialize(seeds, embedder)
    recognizer = IntentRecognizer(
        IntentRuleMatcher.from_yaml(),
        l2=L2VectorMatcher(knowledge, embedder),
        l3=l3,
        rewrite=CompoundRewrite(),
    )

    result = await recognizer.recognize(
        compound, "trace-pipeline-006"
    )

    assert result.source is IntentSource.LLM
    assert result.multi_intent is True
    assert [item.intent for item in result.intents] == ["itinerary_planning", "booking"]


@pytest.mark.asyncio
async def test_pipeline_uses_l3_for_multi_intent_json() -> None:
    """L0 多意图输入必须进入 L3，并保留模型给出的依赖顺序。"""
    l3 = StaticL3(json.dumps({
        "intents": [
            {
                "intent": "travel_application",
                "target_agent": "itineraryManageAgent",
                "confidence": "high",
                "reason": "用户提出新的公务出行申请。",
            },
            {
                "intent": "itinerary_planning",
                "target_agent": "itineraryPlanAgent",
                "confidence": "medium",
                "reason": "用户同时希望后续安排出行计划。",
            },
        ],
        "primary_intent": "travel_application",
        "multi_intent": True,
        "overall_reason": "用户先提出申请，再提出后续行程安排，存在明确先后关系。",
    }, ensure_ascii=False))
    recognizer = IntentRecognizer(IntentRuleMatcher.from_yaml(), l3=l3)

    result = await recognizer.recognize("帮我申请出差，然后规划行程", "trace-pipeline-002")

    assert result.source is IntentSource.LLM
    assert result.multi_intent is True
    assert [item.intent for item in result.intents] == ["travel_application", "itinerary_planning"]


@pytest.mark.parametrize(
    "raw",
    [
        '{"value": 1}',
        '```json\n{"value": 1}\n```',
        '```\n{"value": 1}\n```',
    ],
)
def test_model_json_parser_accepts_plain_and_fenced_json(raw: str) -> None:
    """模型 JSON 解析器应兼容纯 JSON 与 Markdown 代码块包装。"""
    assert parse_model_json(raw) == {"value": 1}


def test_model_json_parser_rejects_non_json() -> None:
    """模型返回普通说明文本时仍应安全抛出 JSON 解析异常。"""
    with pytest.raises(json.JSONDecodeError):
        parse_model_json("不是 JSON")


def test_l3_fenced_json_is_recognized_as_multi_intent() -> None:
    """L3 返回代码块 JSON 时仍应恢复多意图结果。"""
    payload = {
        "intents": [
            {
                "intent": "itinerary_planning",
                "target_agent": "itineraryPlanAgent",
                "confidence": "high",
                "reason": "需要规划行程。",
            },
            {
                "intent": "hotel_search",
                "target_agent": "bookingAgent",
                "confidence": "high",
                "reason": "需要查询酒店。",
            },
        ],
        "primary_intent": "itinerary_planning",
        "multi_intent": True,
        "overall_reason": "包含规划和酒店查询两个意图。",
    }
    recognizer = IntentRecognizer(
        IntentRuleMatcher.from_yaml(),
        l3=StaticL3(f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"),
    )

    import asyncio

    result = asyncio.run(recognizer.recognize("完全无法识别的表达", "trace-fenced-l3"))

    assert result.source is IntentSource.LLM
    assert result.multi_intent is True


@pytest.mark.asyncio
async def test_l0_multi_intent_never_uses_rewrite_before_l3() -> None:
    """L0 多意图必须直接调用 L3，不能因存在改写器而改变原始句子。"""
    class FailingRewrite:
        """若被调用立即失败，用于验证 L0 分支不会触发改写。"""

        async def rewrite(self, text: str, history: str) -> str:
            """测试中不应执行该方法。"""
            raise AssertionError("rewrite_must_not_run")

    recognizer = IntentRecognizer(
        IntentRuleMatcher.from_yaml(), l3=StaticL3(json.dumps({
            "intents": [{
                "intent": "unknown", "target_agent": "masterAgent", "confidence": "low",
                "reason": "当前请求包含多个待确认事项。",
            }],
            "primary_intent": "unknown", "multi_intent": False,
            "overall_reason": "需要先确认多个事项的处理顺序。",
        }, ensure_ascii=False)), rewrite=FailingRewrite()
    )

    result = await recognizer.recognize("帮我查航班，然后预订酒店", "trace-pipeline-004")

    assert result.source is IntentSource.LLM


@pytest.mark.asyncio
async def test_pipeline_returns_unknown_when_l3_is_invalid_or_unavailable() -> None:
    """L3 非 JSON 或未配置时必须安全回退 unknown/masterAgent。"""
    recognizer = IntentRecognizer(IntentRuleMatcher.from_yaml(), l3=StaticL3("不是 JSON"))

    result = await recognizer.recognize("完全无法识别的内容", "trace-pipeline-003")

    assert result.primary_intent == "unknown"
    assert result.intents[0].target_agent == "masterAgent"
    assert result.intents[0].confidence.value == "low"


@pytest.mark.asyncio
async def test_runtime_initializes_full_seed_index_before_accepting_requests() -> None:
    """运行时必须等待全部种子完成 1024 维 embedding 后才向识别器开放索引。"""
    seeds = load_intent_seeds()
    embedder = DeterministicEmbedder({seed.text: _vector(1) for seed in seeds})
    runtime = await create_intent_runtime(embedder, StaticL3("{}"))

    assert runtime.knowledge.dimension == 1024
    assert runtime.knowledge.size == len(seeds)
    assert runtime.knowledge.version == "intent-seed-v1"


@pytest.mark.asyncio
async def test_runtime_batches_seed_embeddings_by_ten() -> None:
    """真实 Provider 支持批量能力时，68 条种子必须分批而不是逐条初始化。"""
    seeds = load_intent_seeds()
    embedder = BatchDeterministicEmbedder({seed.text: _vector(1) for seed in seeds})

    runtime = await create_intent_runtime(embedder, StaticL3("{}"))

    assert runtime.knowledge.size == len(seeds)
    assert [len(batch) for batch in embedder.batches] == [10, 10, 10, 10, 10, 10, 8]
