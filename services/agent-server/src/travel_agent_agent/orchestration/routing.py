# 本文件定义识别结果落库所需的最小路由摘要，不执行意图识别或 Master 推理。
# 定义 RouteAction、RunRoute 和 route_from_recognition，供 LangGraph 主流程持久化状态。
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from travel_agent_agent.intent.result import IntentRecognitionResult
from travel_agent_agent.orchestration.status import RunStatus


class RouteAction(StrEnum):
    """表示可直达子 Agent 或交由 Master 澄清的路由动作。"""

    DIRECT_DISPATCH = "direct_dispatch"
    CLARIFY = "clarify"


@dataclass(frozen=True, slots=True)
class RunRoute:
    """保存可恢复 Run 所需的安全路由摘要。"""

    action: RouteAction
    target_agent: str
    intent_code: str | None
    intent_source: str | None
    diagnostics: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """序列化为 Redis 固定字段字典。"""
        return {
            "action": self.action.value,
            "target_agent": self.target_agent,
            "intent_code": self.intent_code,
            "intent_source": self.intent_source,
            "diagnostics": self.diagnostics,
        }

    @classmethod
    def from_dict(cls, raw: object) -> RunRoute:
        """解析并校验 Redis 中的路由摘要。"""
        if not isinstance(raw, dict):
            raise ValueError("route")
        action = raw.get("action")
        target_agent = raw.get("target_agent")
        intent_code = raw.get("intent_code")
        intent_source = raw.get("intent_source")
        diagnostics = raw.get("diagnostics", {})
        if not isinstance(action, str) or not isinstance(target_agent, str):
            raise ValueError("route")
        if intent_code is not None and not isinstance(intent_code, str):
            raise ValueError("route")
        if intent_source is not None and not isinstance(intent_source, str):
            raise ValueError("route")
        if not isinstance(diagnostics, dict):
            raise ValueError("route")
        return cls(RouteAction(action), target_agent, intent_code, intent_source, diagnostics)


def route_from_recognition(
    recognition: IntentRecognitionResult,
) -> tuple[RunRoute, RunStatus]:
    """将完整识别 Agent 的结果转换为安全路由摘要。"""
    recognized = recognition.intents[0]
    if (
        recognition.multi_intent
        or recognized.confidence.value != "high"
        or recognized.intent == "unknown"
        or recognized.target_agent == "itineraryPlanAgent"
    ):
        return (
            RunRoute(
                RouteAction.CLARIFY,
                "masterAgent",
                recognition.primary_intent,
                recognition.source.value,
                recognition.diagnostics,
            ),
            RunStatus.CLARIFYING,
        )
    return (
        RunRoute(
            RouteAction.DIRECT_DISPATCH,
            recognized.target_agent,
            recognized.intent,
            recognition.source.value,
            recognition.diagnostics,
        ),
        RunStatus.RUNNING,
    )
