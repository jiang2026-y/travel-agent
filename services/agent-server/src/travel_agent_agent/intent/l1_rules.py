# 本文件实现 L1 的配置化正则匹配与跨 Agent 多意图守卫。
# 定义 Rule、RuleOutcome、IntentRuleMatcher，分别表示规则、裁决结果和按优先级执行的 L1 匹配器。
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from travel_agent_agent.intent.catalog import get_intent_definition
from travel_agent_agent.intent.l0_guard import StrongConjunctionGuard
from travel_agent_agent.intent.result import IntentRecognitionResult

_NON_FINAL_COMPOUND_ACTION = re.compile(
    r"(?:规划.*行程.*(?:订|预订).*(?:酒店|房)|(?:订|预订).*(?:酒店|房).*规划.*行程)"
)

@dataclass(frozen=True, slots=True)
class Rule:
    """保存一个按注册顺序执行的 L1 正则规则。"""

    rule_id: str
    intent_code: str
    pattern: re.Pattern[str]
    exclude_pattern: re.Pattern[str] | None

    def matches(self, text: str) -> bool:
        """在存在正向命中且不存在排除词时返回真。"""
        return self.pattern.search(text) is not None and (
            self.exclude_pattern is None or self.exclude_pattern.search(text) is None
        )


class RuleOutcomeKind(StrEnum):
    """区分 L1 未命中、最终命中和需跳过 L2 的跨 Agent 歧义。"""

    MISS = "miss"
    HIT = "hit"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """保存 L1 裁决及最终结果或歧义子句类别。"""

    kind: RuleOutcomeKind
    result: IntentRecognitionResult | None = None
    clause_intents: tuple[str, ...] = ()
    requires_rewrite: bool = False

    @classmethod
    def miss(cls, *, requires_rewrite: bool = False) -> RuleOutcome:
        """创建允许继续 L2 的未命中裁决。"""
        return cls(RuleOutcomeKind.MISS, requires_rewrite=requires_rewrite)


class IntentRuleMatcher:
    """按已确认优先级匹配 L1 规则，两个及以上已识别意图即判定歧义。"""

    def __init__(
        self,
        rules: tuple[Rule, ...],
        conjunction_guard: StrongConjunctionGuard | None = None,
    ) -> None:
        """接收已排序规则和复用的连词守卫。"""
        self._rules = rules
        self._conjunction_guard = conjunction_guard or StrongConjunctionGuard()
        conjunctions = sorted(self._conjunction_guard.strong_conjunctions, key=len, reverse=True)
        self._clause_separator = re.compile(
            r"[，,；;。！？!?]+|并|" + "|".join(map(re.escape, conjunctions))
        )

    @classmethod
    def from_yaml(cls, path: Path | None = None) -> IntentRuleMatcher:
        """读取版本化 YAML 并按文件顺序构造规则，顺序即业务优先级。"""
        source_path = path or Path(__file__).with_name("intent_rules.yml")
        document = yaml.safe_load(source_path.read_text(encoding="utf-8"))
        raw_rules = document.get("rules") if isinstance(document, dict) else None
        if not isinstance(raw_rules, list) or not raw_rules:
            raise ValueError("intent_rules_required")
        rules: list[Rule] = []
        for raw_rule in raw_rules:
            if not isinstance(raw_rule, dict):
                raise ValueError("intent_rule_must_be_mapping")
            rule_id = raw_rule.get("id")
            intent_code = raw_rule.get("intent")
            pattern = raw_rule.get("pattern")
            exclude_pattern = raw_rule.get("exclude_pattern")
            if not all(
                isinstance(value, str) and value for value in (rule_id, intent_code, pattern)
            ):
                raise ValueError("intent_rule_required_field_missing")
            assert isinstance(rule_id, str)
            assert isinstance(intent_code, str)
            assert isinstance(pattern, str)
            get_intent_definition(intent_code)
            if exclude_pattern is not None and not isinstance(exclude_pattern, str):
                raise ValueError("intent_rule_exclude_pattern_invalid")
            rules.append(
                Rule(
                    rule_id=rule_id,
                    intent_code=intent_code,
                    pattern=re.compile(pattern),
                    exclude_pattern=re.compile(exclude_pattern) if exclude_pattern else None,
                )
            )
        return cls(tuple(rules))

    def evaluate(self, text: str | None, trace_id: str) -> RuleOutcome:
        """先执行子句歧义守卫，再按优先级返回首个完整规则命中。"""
        if text is None or not text.strip():
            return RuleOutcome.miss()
        normalized = text.strip()
        clause_intents = self._match_clause_intents(normalized)
        if self._conjunction_guard.is_obvious_multi_intent(normalized):
            return RuleOutcome(
                RuleOutcomeKind.AMBIGUOUS, clause_intents=tuple(clause_intents)
            )
        if len(clause_intents) >= 2:
            return RuleOutcome(RuleOutcomeKind.AMBIGUOUS, clause_intents=tuple(clause_intents))
        if _NON_FINAL_COMPOUND_ACTION.search(normalized):
            return RuleOutcome.miss(requires_rewrite=True)
        for rule in self._rules:
            if rule.matches(normalized):
                return RuleOutcome(
                    RuleOutcomeKind.HIT,
                    result=IntentRecognitionResult.single_rule_hit(
                        rule.intent_code, trace_id, rule.rule_id
                    ),
                    clause_intents=tuple(clause_intents),
                )
        return RuleOutcome.miss()

    def _match_clause_intents(self, text: str) -> list[str]:
        """逐个子句取首个命中类别，保留顺序供多意图歧义判断使用。"""
        matched_intents: list[str] = []
        for clause in self._clause_separator.split(text):
            normalized_clause = clause.strip()
            if not normalized_clause:
                continue
            for rule in self._rules:
                if rule.matches(normalized_clause):
                    matched_intents.append(rule.intent_code)
                    break
        return matched_intents
