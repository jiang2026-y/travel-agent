# 文件职责：装配预订执行子 Agent 的查询、下单、取消、技能与 API Key 工具。
# 定义 BookingAgent 及其 LangGraph 创建与调用方法，写操作统一走 HITL 确认。
from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, ModelCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.agents.base_sub_agent import BaseSubAgent
from travel_agent_agent.agents.booking.api_key_tools import ApiKeyTools
from travel_agent_agent.agents.booking.skill_tools import SkillTools
from travel_agent_agent.agents.booking.tools import TuniuBookingTools
from travel_agent_agent.agents.common.circuit_breaker import (
    configured_circuit_breaker_middleware,
)
from travel_agent_agent.agents.common.context_efficiency import (
    build_context_efficiency_middleware,
)
from travel_agent_agent.agents.common.result_cards import build_result_card_middleware
from travel_agent_agent.agents.common.session_context import (
    bind_session_context,
    reset_session_context,
)
from travel_agent_agent.agents.common.tool_errors import build_tool_error_middleware
from travel_agent_agent.agents.common.tool_progress import build_tool_progress_middleware
from travel_agent_agent.agents.itinerary_manage.time_hook import DynamicTimeInjectionHook
from travel_agent_agent.agents.itinerary_manage.tools import BookingReadTools, QueryUserInfoTools
from travel_agent_agent.agents.master.memory_tools import PreferenceMemoryTools
from travel_agent_agent.agents.master.summary import build_summary_middleware
from travel_agent_agent.core.prompt_loader import load_prompt
from travel_agent_agent.core.settings import Settings
from travel_agent_agent.infrastructure.memory_client import BailianMemoryClient

WRITE_TOOL_DECISIONS: dict[str, Any] = {
    "create_tuniu_flight_order": {
        "allowed_decisions": ["approve", "reject"],
        "description": "创建机票订单需要用户确认。",
    },
    "create_tuniu_train_order": {
        "allowed_decisions": ["approve", "reject"],
        "description": "创建火车票订单需要用户确认。",
    },
    "create_tuniu_hotel_order": {
        "allowed_decisions": ["approve", "reject"],
        "description": "创建酒店订单需要用户确认。",
    },
    "cancel_booking": {
        "allowed_decisions": ["approve", "reject"],
        "description": "取消已有预订需要用户确认。",
    },
}


class BookingAgent(BaseSubAgent):
    """预订执行智能体：查询、下单、取消，并在写入前要求用户确认。"""

    max_iters = 10

    def __init__(
        self,
        model: BaseChatModel,
        settings: Settings,
        context: AgentContext,
        *,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        summary_model: BaseChatModel | None = None,
        memory_client: BailianMemoryClient | None = None,
    ) -> None:
        """装配差旅单、预订记录、用户资料、API Key、技能与途牛工具。"""
        super().__init__(settings, context)
        self.booking_read_tools = BookingReadTools(self.client)
        self.profile_tools = QueryUserInfoTools(self.client)
        self.api_key_tools = ApiKeyTools(self.client)
        self.skill_tools = SkillTools(settings, self.client)
        self.tuniu_tools = TuniuBookingTools(settings, self.client)
        tools: list[Any] = [
            *self.shared_read_tools(),
            *self.booking_read_tools.as_tools(),
            *self.profile_tools.as_tools(),
            *self.api_key_tools.as_tools(),
            *self.skill_tools.as_tools(),
        ]
        if settings.tuniu_call_enabled:
            tools.extend(self.tuniu_tools.as_tools())
        if memory_client is not None:
            tools.extend(
                PreferenceMemoryTools(memory_client, lambda: self.context).as_tools()
            )
        # 查询与下单都依赖相对日期（如"明天"），因此与行程管理一致注入动态中国时间。
        middleware: list[Any] = [
            *configured_circuit_breaker_middleware(),
            *build_context_efficiency_middleware(),
            *build_tool_progress_middleware("bookingAgent", lambda: self.context),
            *build_result_card_middleware(lambda: self.context),
            DynamicTimeInjectionHook(),
        ]
        middleware.append(build_tool_error_middleware())
        summary_middleware = build_summary_middleware(summary_model)
        if summary_middleware is not None:
            middleware.append(summary_middleware)
        if settings.tuniu_call_enabled:
            middleware.append(HumanInTheLoopMiddleware(WRITE_TOOL_DECISIONS))
        middleware.append(ModelCallLimitMiddleware(run_limit=self.max_iters, exit_behavior="end"))
        self.graph = create_agent(
            model=model,
            tools=tools,
            system_prompt=load_prompt("prompts/booking-agent-system.md"),
            middleware=middleware,
            checkpointer=checkpointer,
            name="bookingAgent",
        )

    async def ainvoke(self, message: str, session_id: str | None = None) -> Any:
        """执行一次预订查询或确认后的写入请求。"""
        token = bind_session_context(self.session_context)
        try:
            return await self.graph.ainvoke(
                {"messages": [{"role": "user", "content": message}]},
                config={"configurable": {"thread_id": session_id}} if session_id else None,
            )
        finally:
            reset_session_context(token)
