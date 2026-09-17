# 本文件实现 L2 意图向量索引、启动灌库和 Top-K 相似度判定。
# 定义 EmbeddingPort、IntentSeed、VectorHit、InMemoryIntentKnowledge 与 L2VectorMatcher，
# 分别表示 embedding 端口、种子语料、检索结果、1024 维内存库和 L2 绝对阈值/margin 裁决器。
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from travel_agent_agent.intent.catalog import get_intent_definition
from travel_agent_agent.intent.result import IntentRecognitionResult


class EmbeddingPort(Protocol):
    """定义独立意图 embedding Provider 的异步调用边界。"""

    async def embed(self, text: str) -> list[float]: ...


class BatchEmbeddingPort(EmbeddingPort, Protocol):
    """定义一次最多十条文本的批量 embedding 调用边界，用于启动灌入种子。"""

    async def embed_many(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class IntentSeed:
    """保存一个意图种子、版本和用于路由的稳定样本编号。"""

    sample_id: str
    intent_code: str
    text: str
    version: str = "intent-seed-v1"


@dataclass(frozen=True, slots=True)
class VectorHit:
    """保存向量检索的原始分数和意图元数据。"""

    sample_id: str
    intent_code: str
    text: str
    score: float
    version: str


class InMemoryIntentKnowledge:
    """保存独立于景点/政策 RAG 的 1024 维意图向量索引。"""

    def __init__(self, dimension: int = 1024) -> None:
        """创建空索引；维度不是 1024 时立即拒绝，防止库混用。"""
        if dimension != 1024:
            raise ValueError("intent_embedding_dimension_must_be_1024")
        self.dimension = dimension
        self._entries: list[tuple[IntentSeed, tuple[float, ...]]] = []
        self.version: str | None = None

    @property
    def size(self) -> int:
        """返回已灌入种子数量。"""
        return len(self._entries)

    async def initialize(self, seeds: list[IntentSeed], embedder: EmbeddingPort) -> None:
        """启动时按每批十条生成 1024 维向量并原子替换索引，失败不留下半成品。"""
        if not seeds:
            raise ValueError("intent_seeds_required")
        staged: list[tuple[IntentSeed, tuple[float, ...]]] = []
        versions: set[str] = set()
        vectors = await _embed_seed_vectors(seeds, embedder)
        for seed, vector in zip(seeds, vectors, strict=True):
            get_intent_definition(seed.intent_code)
            if len(vector) != self.dimension:
                raise ValueError("intent_embedding_dimension_invalid")
            if not all(math.isfinite(value) for value in vector):
                raise ValueError("intent_embedding_value_invalid")
            staged.append((seed, tuple(vector)))
            versions.add(seed.version)
        if len(versions) != 1:
            raise ValueError("intent_seed_version_mismatch")
        self._entries = staged
        self.version = versions.pop()

    async def retrieve(self, query: str, top_k: int, embedder: EmbeddingPort) -> list[VectorHit]:
        """检索全部候选后按余弦相似度降序返回 Top-K，不在底层做阈值过滤。"""
        if not self._entries:
            return []
        if top_k < 1:
            raise ValueError("top_k_must_be_positive")
        query_vector = await embedder.embed(query)
        if len(query_vector) != self.dimension:
            raise ValueError("intent_embedding_dimension_invalid")
        scored = [
            VectorHit(
                seed.sample_id,
                seed.intent_code,
                seed.text,
                _cosine(query_vector, vector),
                seed.version,
            )
            for seed, vector in self._entries
        ]
        return sorted(scored, key=lambda item: item.score, reverse=True)[:top_k]


class L2VectorMatcher:
    """执行 0.75 Top-1 阈值、同意图 Top-2 与不大于 0.05 分差判定。"""

    def __init__(
        self,
        knowledge: InMemoryIntentKnowledge,
        embedder: EmbeddingPort,
        top_k: int = 3,
        score_threshold: float = 0.75,
        score_margin: float = 0.05,
    ) -> None:
        """绑定独立意图知识库与 embedding Provider。"""
        self.knowledge = knowledge
        self.embedder = embedder
        self.top_k = top_k
        self.score_threshold = score_threshold
        self.score_margin = score_margin
        self.last_diagnostics: dict[str, object] = {"status": "not_run"}

    async def match(
        self, text: str, trace_id: str, *, allow_direct_hit: bool = True
    ) -> IntentRecognitionResult | None:
        """按流程图裁决；任何检索异常、空结果或歧义均返回未命中交给 L3。"""
        if not text or not text.strip():
            self.last_diagnostics = {"status": "skipped_empty"}
            return None
        try:
            docs = await self.knowledge.retrieve(text.strip(), self.top_k, self.embedder)
        except Exception:
            self.last_diagnostics = {"status": "error"}
            return None
        if not docs:
            self.last_diagnostics = {"status": "miss", "reason": "no_candidates"}
            return None
        top = docs[0]
        self.last_diagnostics = {
            "status": "evaluating",
            "top1_intent": top.intent_code,
            "top1_score": round(top.score, 6),
            "threshold": self.score_threshold,
        }
        if top.score < self.score_threshold or not top.intent_code:
            self.last_diagnostics["status"] = "miss"
            self.last_diagnostics["reason"] = "top1_below_threshold"
            return None
        if len(docs) < 2:
            self.last_diagnostics["status"] = "miss"
            self.last_diagnostics["reason"] = "top2_missing"
            return None
        second = docs[1]
        self.last_diagnostics.update({
            "top2_intent": second.intent_code,
            "top2_score": round(second.score, 6),
            "margin": round(top.score - second.score, 6),
        })
        if not second.intent_code:
            self.last_diagnostics["status"] = "miss"
            self.last_diagnostics["reason"] = "top2_missing_intent"
            return None
        margin = top.score - second.score
        if second.intent_code != top.intent_code and margin <= self.score_margin:
            self.last_diagnostics["status"] = "ambiguous"
            self.last_diagnostics["reason"] = "different_intents_small_margin"
            return None
        if not allow_direct_hit:
            self.last_diagnostics["status"] = "miss"
            self.last_diagnostics["reason"] = "compound_action_requires_rewrite"
            return None
        definition = get_intent_definition(top.intent_code)
        self.last_diagnostics["status"] = "hit"
        return IntentRecognitionResult.single_vector_hit(
            definition.code, trace_id, top.sample_id, top.score, top.text, top.version
        )


def _cosine(left: list[float], right: tuple[float, ...]) -> float:
    """计算余弦相似度；零向量安全返回 0，避免 NaN 进入阈值判断。"""
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


async def _embed_seed_vectors(
    seeds: list[IntentSeed], embedder: EmbeddingPort
) -> list[list[float]]:
    """优先使用最多十条的批量调用；旧测试替身缺少该能力时保留逐条兼容。"""
    batch_embed = getattr(embedder, "embed_many", None)
    if not callable(batch_embed):
        return [await embedder.embed(seed.text) for seed in seeds]
    vectors: list[list[float]] = []
    for offset in range(0, len(seeds), 10):
        batch = seeds[offset : offset + 10]
        batch_vectors = await batch_embed([seed.text for seed in batch])
        if len(batch_vectors) != len(batch):
            raise ValueError("intent_embedding_batch_size_invalid")
        vectors.extend(batch_vectors)
    return vectors


def load_intent_seeds(path: Path | None = None) -> list[IntentSeed]:
    """读取版本化 YAML，并为每条问句生成稳定样本编号。"""
    source_path = path or Path(__file__).with_name("intent-seed.yml")
    document = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("intent_seed_document_invalid")
    version = document.get("version")
    intents = document.get("intent")
    if not isinstance(version, str) or not version or not isinstance(intents, dict) or not intents:
        raise ValueError("intent_seed_required_field_missing")
    seeds: list[IntentSeed] = []
    for intent_code, examples in intents.items():
        get_intent_definition(intent_code)
        if not isinstance(examples, list) or not examples:
            raise ValueError("intent_seed_examples_required")
        for index, text in enumerate(examples, start=1):
            if not isinstance(text, str) or not text.strip():
                raise ValueError("intent_seed_text_invalid")
            seeds.append(IntentSeed(f"{intent_code}-{index:03d}", intent_code, text, version))
    return seeds
