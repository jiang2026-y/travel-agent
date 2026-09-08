# 本文件实现子 Agent 配置注册、懒加载和自然语言调用协议。
# 定义 SubAgentRequest、SubAgentResult、SubAgentConfig 和 SubAgentProvider。
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from travel_agent_agent.agents.base import AgentContext


def disabled_sub_agent_factory(name: str) -> SubAgentFactory:
    """创建只读占位子 Agent 工厂；未接入真实业务前不产生外部副作用。"""
    async def handler(request: SubAgentRequest) -> SubAgentResult:
        """返回能力待接入提示，并保留会话标识。"""
        del request
        return SubAgentResult(f"{name} 当前仅完成注册，业务能力尚未接入。")

    return lambda: handler


def default_sub_agent_configs() -> tuple[SubAgentConfig, ...]:
    """返回四个已确认子 Agent 的懒加载配置，不注册预订和报销 Agent。"""
    names = (
        "itinerary_manage_agent",
        "itinerary_plan_agent",
        "booking_agent",
        "itinerary_review_agent",
        "info_agent",
    )
    return tuple(SubAgentConfig(name, disabled_sub_agent_factory(name)) for name in names)


@dataclass(frozen=True, slots=True)
class SubAgentRequest:
    """保存传给子 Agent 的自然语言任务和结构化关联上下文。"""

    message: str
    session_id: str | None
    context: AgentContext
    resume_value: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class SubAgentResult:
    """保存子 Agent 的原始自然语言结果和可续用会话标识。"""

    content: str
    session_id: str | None = None
    pending_interaction: dict[str, object] | None = None


SubAgentFactory = Callable[[], Callable[[SubAgentRequest], Awaitable[SubAgentResult]]]


@dataclass(frozen=True, slots=True)
class SubAgentConfig:
    """保存子 Agent 工具名、启用状态和延迟创建工厂。"""

    key: str
    factory: SubAgentFactory
    enabled: bool = True


class SubAgentProvider:
    """按工具名懒加载并缓存四个已登记子 Agent，拒绝未登记能力。"""

    def __init__(self, configs: tuple[SubAgentConfig, ...]) -> None:
        """只保存配置，不在初始化阶段实例化任何子 Agent。"""
        self._configs = {config.key: config for config in configs}
        self._instances: dict[str, Callable[[SubAgentRequest], Awaitable[SubAgentResult]]] = {}

    @property
    def loaded_keys(self) -> tuple[str, ...]:
        """返回已实际创建的子 Agent 工具名。"""
        return tuple(self._instances)

    async def invoke(self, key: str, request: SubAgentRequest) -> SubAgentResult:
        """按 key 首次创建并调用子 Agent，后续复用同一实例。"""
        config = self._configs.get(key)
        if config is None or not config.enabled:
            return SubAgentResult("当前能力暂未启用，无法执行该请求。")
        handler = self._instances.get(key)
        if handler is None:
            handler = config.factory()
            self._instances[key] = handler
        return await handler(request)
