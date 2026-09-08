# 本文件编排 L0、L1、L2 和 L3 的统一意图识别流程。
# 定义 RewritePort、L3Port、IntentRecognizer，分别表示一次问题改写端口、
# 严格 JSON LLM 端口和三层短路编排器。
from __future__ import annotations

from typing import Protocol

from travel_agent_agent.core.json_utils import parse_model_json
from travel_agent_agent.intent.l0_guard import StrongConjunctionGuard
from travel_agent_agent.intent.l1_rules import IntentRuleMatcher, RuleOutcomeKind
from travel_agent_agent.intent.l2_vector import L2VectorMatcher
from travel_agent_agent.intent.result import (
    IntentConfidence,
    IntentRecognitionResult,
    IntentSource,
    L3ModelResponse,
    RecognizedIntent,
)


class RewritePort(Protocol):
    """定义最多一次的问题改写边界。"""

    async def rewrite(self, text: str, history: str) -> str: ...


class L3Port(Protocol):
    """定义只返回严格 JSON 文本的 L3 模型边界。"""

    async def classify(self, rewritten_question: str, history: str) -> str: ...


class IntentRecognizer:
    """执行 L0→L1→L2→一次改写→L0/L1/L2→L3 的统一短路流程。"""

    def __init__(
        self,
        l1: IntentRuleMatcher,
        l2: L2VectorMatcher | None = None,
        l3: L3Port | None = None,
        rewrite: RewritePort | None = None,
        l0: StrongConjunctionGuard | None = None,
    ) -> None:
        """注入各层端口；未配置真实 L2/L3 时保持安全禁用。"""
        self.l0 = l0 or StrongConjunctionGuard()
        self.l1 = l1
        self.l2 = l2
        self.l3 = l3
        self.rewrite = rewrite

    async def recognize(
        self, text: str | None, trace_id: str, history: str = ""
    ) -> IntentRecognitionResult:
        """返回最终统一结果；外部能力缺失时安全回退 unknown，不伪造命中。"""
        result, _ = await self.recognize_with_rewrite(text, trace_id, history)
        return result

    async def recognize_with_rewrite(
        self, text: str | None, trace_id: str, history: str = ""
    ) -> tuple[IntentRecognitionResult, str]:
        """执行完整识别并返回最终结果及实际进入 Master 的改写后问题。"""
        original = text or ""
        diagnostics: dict[str, object] = {}
        first, may_rewrite = await self._short_circuit(original, trace_id, diagnostics)
        if first is not None:
            return self._with_diagnostics(first, diagnostics), original
        if may_rewrite and self.rewrite is not None and original.strip():
            diagnostics["rewrite_called"] = True
            try:
                rewritten = (await self.rewrite.rewrite(original.strip(), history)).strip()
            except Exception:
                rewritten = ""
            if rewritten:
                diagnostics["rewrite_changed"] = rewritten != original.strip()
                second, _ = await self._short_circuit(rewritten, trace_id, diagnostics, "rewritten_")
                if second is not None:
                    return self._with_diagnostics(second, diagnostics), rewritten
                original = rewritten
            else:
                diagnostics["rewrite_changed"] = False
                diagnostics["rewrite_status"] = "failed_or_empty"
        if self.l3 is not None:
            l3_result = await self._classify_l3(self.l3, original, history, trace_id)
            if l3_result is not None:
                return self._with_diagnostics(l3_result, diagnostics), original
        return self._with_diagnostics(
            self._unknown(trace_id, "当前问题信息不足，需要进一步确认你的需求。"), diagnostics
        ), original

    async def _short_circuit(
        self, text: str, trace_id: str, diagnostics: dict[str, object], prefix: str = ""
    ) -> tuple[IntentRecognitionResult | None, bool]:
        """执行 L0/L1/L2；仅 L1/L2 普通未命中允许后续改写，歧义直达 L3。"""
        if self.l0.is_obvious_multi_intent(text):
            diagnostics[f"{prefix}l0"] = "multi"
            return None, False
        diagnostics[f"{prefix}l0"] = "single"
        outcome = self.l1.evaluate(text, trace_id)
        if outcome.kind is RuleOutcomeKind.HIT:
            diagnostics[f"{prefix}l1"] = "hit"
            diagnostics[f"{prefix}l2"] = "skipped"
            return outcome.result, False
        if outcome.kind is RuleOutcomeKind.AMBIGUOUS:
            diagnostics[f"{prefix}l1"] = "ambiguous"
            diagnostics[f"{prefix}l2"] = "skipped"
            return None, False
        diagnostics[f"{prefix}l1"] = "miss"
        if self.l2 is not None:
            result = await self.l2.match(
                text, trace_id, allow_direct_hit=not outcome.requires_rewrite
            )
            diagnostics[f"{prefix}l2"] = dict(self.l2.last_diagnostics)
            is_ambiguous = self.l2.last_diagnostics.get("status") == "ambiguous"
            return result, result is None and not is_ambiguous
        diagnostics[f"{prefix}l2"] = "unavailable"
        return None, True

    @staticmethod
    def _with_diagnostics(result: IntentRecognitionResult, diagnostics: dict[str, object]) -> IntentRecognitionResult:
        """仅附加诊断字段，不改变原有识别和路由结果。"""
        payload = dict(diagnostics)
        payload["final"] = {
            "source": result.source.value,
            "multi_intent": result.multi_intent,
            "intents": [item.intent for item in result.intents],
        }
        return result.model_copy(update={"diagnostics": payload})

    async def _classify_l3(
        self, l3: L3Port, text: str, history: str, trace_id: str
    ) -> IntentRecognitionResult | None:
        """解析 L3 严格 JSON 并补充服务端来源、追踪和模型结果元数据。"""
        try:
            raw = await l3.classify(text, history)
            parsed = L3ModelResponse.model_validate(parse_model_json(raw))
        except Exception:
            return None
        return IntentRecognitionResult(
            **parsed.model_dump(),
            source=IntentSource.LLM,
            trace_id=trace_id,
        )

    @staticmethod
    def _unknown(trace_id: str, reason: str) -> IntentRecognitionResult:
        """创建固定路由 masterAgent 的低置信 unknown 结果。"""
        intent = RecognizedIntent(
            intent="unknown",
            target_agent="masterAgent",
            confidence=IntentConfidence.LOW,
            reason=reason,
        )
        return IntentRecognitionResult(
            intents=(intent,),
            primary_intent="unknown",
            multi_intent=False,
            overall_reason=reason,
            source=IntentSource.LLM,
            trace_id=trace_id,
        )
