# 本文件定义 L0～L3 共用的意图识别结果模型。
# 定义 IntentSource、IntentConfidence、RecognizedIntent、IntentRecognitionResult 与
# L3ModelResponse，分别表达识别来源、置信等级、单个意图、服务端统一结果和 L3 模型严格 JSON 输出。
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from travel_agent_agent.intent.catalog import get_intent_definition

_FORBIDDEN_REASON_TERMS = (
    "target_agent",
    "primary_intent",
    "itineraryPlanAgent",
    "itineraryManageAgent",
    "bookingAgent",
    "reimbursementAgent",
    "masterAgent",
    "infoAgent",
    "plan_itinerary",
    "query_travel_order",
)


def _validate_user_reason(value: str) -> str:
    """拒绝向用户暴露内部字段、Bean 名或工具函数名。"""
    if any(term in value for term in _FORBIDDEN_REASON_TERMS):
        raise ValueError("reason_contains_internal_term")
    return value


class IntentSource(StrEnum):
    """表示最终识别结果的产生层级。"""

    RULE = "RULE"
    VECTOR = "VECTOR"
    LLM = "LLM"


class IntentConfidence(StrEnum):
    """表示可展示的意图置信等级。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RecognizedIntent(BaseModel):
    """保存一个经目录白名单校验的意图及其用户可读理由。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: str = Field(min_length=1, max_length=64)
    target_agent: str = Field(min_length=1, max_length=64)
    confidence: IntentConfidence
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_target_agent(self) -> RecognizedIntent:
        """确保任何意图均只能指向目录预先定义的 camelCase Agent 名。"""
        definition = get_intent_definition(self.intent)
        if self.target_agent != definition.target_agent:
            raise ValueError("intent_target_agent_mapping_invalid")
        _validate_user_reason(self.reason)
        return self


class L3ModelResponse(BaseModel):
    """严格表示 L3 模型允许返回的 JSON，不承载运行时追踪或检索元数据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intents: tuple[RecognizedIntent, ...] = Field(min_length=1, max_length=16)
    primary_intent: str = Field(min_length=1, max_length=64)
    multi_intent: bool
    overall_reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_consistency(self) -> L3ModelResponse:
        """校验主意图和多意图标记与数组内容完全一致。"""
        intent_codes = {item.intent for item in self.intents}
        if self.primary_intent not in intent_codes:
            raise ValueError("primary_intent_not_in_intents")
        if self.multi_intent != (len(self.intents) > 1):
            raise ValueError("multi_intent_inconsistent")
        _validate_user_reason(self.overall_reason)
        return self


class IntentRecognitionResult(L3ModelResponse):
    """保存服务端可追踪的统一识别结果，供后续编排与 SSE 使用。"""

    source: IntentSource
    trace_id: str = Field(min_length=1, max_length=128)
    matched_rule_or_sample_id: str | None = Field(default=None, max_length=128)
    sample_version: str | None = Field(default=None, max_length=128)
    score: float | None = Field(default=None, ge=0, le=1)
    diagnostics: dict[str, object] = Field(default_factory=dict)

    @classmethod
    def single_rule_hit(
        cls, intent_code: str, trace_id: str, rule_id: str
    ) -> IntentRecognitionResult:
        """根据 L1 规则命中创建单意图高置信统一结果。"""
        definition = get_intent_definition(intent_code)
        recognized_intent = RecognizedIntent(
            intent=definition.code,
            target_agent=definition.target_agent,
            confidence=IntentConfidence.HIGH,
            reason=f"用户表达与{definition.display_name}诉求相符。",
        )
        return cls(
            intents=(recognized_intent,),
            primary_intent=definition.code,
            multi_intent=False,
            overall_reason=f"当前输入主要是{definition.display_name}诉求。",
            source=IntentSource.RULE,
            trace_id=trace_id,
            matched_rule_or_sample_id=rule_id,
        )

    @classmethod
    def single_vector_hit(
        cls,
        intent_code: str,
        trace_id: str,
        sample_id: str,
        score: float,
        sample_text: str,
        sample_version: str,
    ) -> IntentRecognitionResult:
        """根据 L2 Top-1 命中创建带分数、样本版本和脱敏依据的统一结果。"""
        definition = get_intent_definition(intent_code)
        recognized_intent = RecognizedIntent(
            intent=definition.code,
            target_agent=definition.target_agent,
            confidence=IntentConfidence.HIGH if score >= 0.85 else IntentConfidence.MEDIUM,
            reason=f"用户表达与{definition.display_name}语义相符。",
        )
        return cls(
            intents=(recognized_intent,),
            primary_intent=definition.code,
            multi_intent=False,
            overall_reason=f"根据语义相似度匹配到{definition.display_name}。",
            source=IntentSource.VECTOR,
            trace_id=trace_id,
            matched_rule_or_sample_id=sample_id,
            sample_version=sample_version,
            score=score,
        )
