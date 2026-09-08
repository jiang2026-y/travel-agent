# 本文件验证 L0 强连词守卫、L1 规则优先级、跨 Agent 歧义与统一结果白名单。
# 定义 L0 阈值、L1 分类、排除词、同 Agent 保留单意图、跨 Agent 歧义及 L3 JSON 校验测试函数。
import pytest
from pydantic import ValidationError

from travel_agent_agent.intent.l0_guard import StrongConjunctionGuard
from travel_agent_agent.intent.l1_rules import IntentRuleMatcher, RuleOutcomeKind
from travel_agent_agent.intent.result import L3ModelResponse


@pytest.fixture
def matcher() -> IntentRuleMatcher:
    """加载生产规则配置，保证测试覆盖真实注册顺序。"""
    return IntentRuleMatcher.from_yaml()


def test_l0_requires_minimum_length_and_conjunction_offset() -> None:
    """短句和句首连词不应触发 L0 多意图直达 L3。"""
    guard = StrongConjunctionGuard()

    assert guard.is_obvious_multi_intent("顺便问下餐标") is False
    assert guard.is_obvious_multi_intent("另外你好，帮我查航班") is False
    assert guard.is_obvious_multi_intent("帮我查航班，然后再查酒店") is True


def test_l1_marks_two_recognized_intents_ambiguous(matcher: IntentRuleMatcher) -> None:
    """未触发 L0 但拆分后识别出两个意图时，也必须转入 L3。"""
    outcome = matcher.evaluate("帮我规划杭州行程并查航班", "trace-intent-001")

    assert outcome.kind is RuleOutcomeKind.AMBIGUOUS
    assert outcome.clause_intents == ("itinerary_planning", "flight_search")


def test_l1_exclusion_routes_reimbursement_policy_question_to_policy(
    matcher: IntentRuleMatcher,
) -> None:
    """含报销政策的提问必须避开报销意图并匹配政策查询。"""
    outcome = matcher.evaluate("差旅报销政策和标准是什么", "trace-intent-002")

    assert outcome.kind is RuleOutcomeKind.HIT
    assert outcome.result is not None
    assert outcome.result.primary_intent == "policy_query"


def test_l1_marks_same_agent_multiple_intents_ambiguous(matcher: IntentRuleMatcher) -> None:
    """未达到 L0 阈值但识别到两个意图时，同一 Agent 也必须交由 L3。"""
    outcome = matcher.evaluate("查航班并查酒店", "trace-intent-003")

    assert outcome.kind is RuleOutcomeKind.AMBIGUOUS
    assert outcome.clause_intents == ("flight_search", "hotel_search")


def test_l1_marks_cross_agent_clauses_ambiguous(matcher: IntentRuleMatcher) -> None:
    """查询航班并查看政策横跨两个 Agent，必须跳过 L2 交由 L3。"""
    outcome = matcher.evaluate("帮我查航班，然后问下餐标标准", "trace-intent-004")

    assert outcome.kind is RuleOutcomeKind.AMBIGUOUS
    assert outcome.clause_intents == ("flight_search", "policy_query")


def test_l0_guard_skips_l1_even_when_only_one_clause_is_known(matcher: IntentRuleMatcher) -> None:
    """明显多意图输入必须直接交 L3，不能被全文优先级截断成单一意图。"""
    outcome = matcher.evaluate("帮我查航班，然后再问问", "trace-intent-005")

    assert outcome.kind is RuleOutcomeKind.AMBIGUOUS


def test_l1_does_not_short_circuit_compound_planning_and_hotel_booking(
    matcher: IntentRuleMatcher,
) -> None:
    """状态陈述混合规划与订酒店时，L1 不得只凭规划关键词判为最终单意图。"""
    outcome = matcher.evaluate("审批通过了，帮我规划下杭州行程订个酒店", "trace-intent-006")

    assert outcome.kind is RuleOutcomeKind.MISS
    assert outcome.requires_rewrite is True


def test_l3_response_rejects_extra_fields_and_invalid_agent_mapping() -> None:
    """L3 原始 JSON 只能使用白名单映射且禁止额外字段。"""
    valid = {
        "intents": [
            {
                "intent": "booking",
                "target_agent": "bookingAgent",
                "confidence": "high",
                "reason": "用户明确要求确认已选方案。",
            }
        ],
        "primary_intent": "booking",
        "multi_intent": False,
        "overall_reason": "当前诉求为确认已选方案。",
    }
    assert L3ModelResponse.model_validate(valid).primary_intent == "booking"

    invalid = {**valid, "unexpected": "value"}
    with pytest.raises(ValidationError):
        L3ModelResponse.model_validate(invalid)

    invalid_mapping = {**valid, "intents": [{**valid["intents"][0], "target_agent": "infoAgent"}]}
    with pytest.raises(ValidationError, match="intent_target_agent_mapping_invalid"):
        L3ModelResponse.model_validate(invalid_mapping)

    invalid_reason = {**valid, "overall_reason": "内部字段 primary_intent 不得出现。"}
    with pytest.raises(ValidationError, match="reason_contains_internal_term"):
        L3ModelResponse.model_validate(invalid_reason)
