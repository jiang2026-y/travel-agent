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
    """返回五个可注册子 Agent 的懒加载配置，规划与审核保持禁用。"""
    configs = (
        SubAgentConfig(
            "itinerary_manage_agent",
            disabled_sub_agent_factory("itinerary_manage_agent"),
            description="差旅单全生命周期管理：提交、查询、修改、取消出差申请与审批状态。",
        ),
        SubAgentConfig(
            "booking_agent",
            disabled_sub_agent_factory("booking_agent"),
            description="机票、酒店、火车票查询与预订执行，以及已有预订的取消。",
        ),
        SubAgentConfig(
            "info_agent",
            disabled_sub_agent_factory("info_agent"),
            description="差旅政策、景点、签证与目的地公共信息查询。",
        ),
        SubAgentConfig(
            "itinerary_plan_agent",
            disabled_sub_agent_factory("itinerary_plan_agent"),
            enabled=False,
            description="行程规划能力当前未接入。",
        ),
        SubAgentConfig(
            "itinerary_review_agent",
            disabled_sub_agent_factory("itinerary_review_agent"),
            enabled=False,
            description="行程审核能力当前未接入。",
        ),
    )
    return configs


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
    """保存子 Agent 工具名、启用状态、工具说明和延迟创建工厂。"""

    key: str
    factory: SubAgentFactory
    enabled: bool = True
    description: str = ""

    @property
    def tool_description(self) -> str:
        """返回暴露给主模型的中文工具说明。"""
        if self.description:
            return self.description
        return f"调用 {self.key} 处理对应的差旅子任务。"


class SubAgentProvider:
    """按工具名懒加载并缓存已登记子 Agent，拒绝未登记或未启用能力。"""

    def __init__(self, configs: tuple[SubAgentConfig, ...]) -> None:
        """只保存配置，不在初始化阶段实例化任何子 Agent。"""
        self._configs = {config.key: config for config in configs}
        self._instances: dict[str, Callable[[SubAgentRequest], Awaitable[SubAgentResult]]] = {}

    @property
    def loaded_keys(self) -> tuple[str, ...]:
        """返回已实际创建的子 Agent 工具名。"""
        return tuple(self._instances)

    @property
    def enabled_configs(self) -> tuple[SubAgentConfig, ...]:
        """按注册顺序返回已启用的子 Agent 配置，供主模型工具注册使用。"""
        return tuple(config for config in self._configs.values() if config.enabled)

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
