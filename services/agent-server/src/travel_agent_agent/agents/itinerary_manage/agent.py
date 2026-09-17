# 本文件创建行程管理 ReAct Agent 的时间感知骨架，不注册未实现的业务写工具。
# 定义 ItineraryManageAgent，固定 qwen3.7-plus、非思考和十次模型调用上限。
from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, ModelCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.agents.base_sub_agent import BaseSubAgent
from travel_agent_agent.agents.common.circuit_breaker import (
    configured_circuit_breaker_middleware,
)
from travel_agent_agent.agents.common.context_efficiency import (
    build_context_efficiency_middleware,
)
from travel_agent_agent.agents.common.session_context import (
    bind_session_context,
    reset_session_context,
)
from travel_agent_agent.agents.common.tool_errors import build_tool_error_middleware
from travel_agent_agent.agents.common.tool_progress import build_tool_progress_middleware
from travel_agent_agent.agents.common.travel_order_read_tools import TravelOrderReadTools
from travel_agent_agent.agents.itinerary_manage.client import TravelManageApiClient
from travel_agent_agent.agents.itinerary_manage.time_hook import DynamicTimeInjectionHook
from travel_agent_agent.agents.itinerary_manage.tools import (
    BookingReadTools,
    BookingWriteTools,
    PolicyTools,
    QueryUserInfoTools,
    TravelOrderConflictTools,
    TravelOrderWriteTools,
)
from travel_agent_agent.agents.master.summary import build_summary_middleware
from travel_agent_agent.core.prompt_loader import load_prompt
from travel_agent_agent.core.settings import Settings


class ItineraryManageAgent(BaseSubAgent):
    """封装时间感知的行程管理 ReAct Agent；业务工具将在后续受控注册。"""

    model_name = "qwen3.7-plus"
    enable_thinking = False
    max_iters = 10

    def __init__(
        self,
        model: BaseChatModel,
        settings: Settings | None = None,
        context: AgentContext | None = None,
        *,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        tools: list[Any] | None = None,
        summary_model: BaseChatModel | None = None,
    ) -> None:
        """创建静态提示词优先、动态时间第二条注入的 LangGraph Agent。"""
        if settings is not None and context is not None:
            super().__init__(settings, context)
        self.time_hook = DynamicTimeInjectionHook()
        middleware: list[Any] = [
            *configured_circuit_breaker_middleware(),
            *build_context_efficiency_middleware(),
            *build_tool_progress_middleware("itineraryManageAgent", lambda: self.context),
            self.time_hook,
            build_tool_error_middleware(),
        ]
        summary_middleware = build_summary_middleware(summary_model)
        if summary_middleware is not None:
            middleware.append(summary_middleware)
        middleware.append(
            HumanInTheLoopMiddleware(
                {
                    "submit_travel_approval": {
                        "allowed_decisions": ["approve", "reject"],
                        "description": "提交差旅申请需要用户确认。",
                    },
                    "cancel_travel_order": {
                        "allowed_decisions": ["approve", "reject"],
                        "description": "取消差旅申请需要用户确认。",
                    },
                    "modify_travel_order": {
                        "allowed_decisions": ["approve", "reject"],
                        "description": "修改差旅申请需要用户确认。",
                    },
                    "cancel_booking": {
                        "allowed_decisions": ["approve", "reject"],
                        "description": "取消预订需要用户确认。",
                    },
                }
            )
        )
        middleware.append(ModelCallLimitMiddleware(run_limit=self.max_iters, exit_behavior="end"))
        self.graph = create_agent(
            model=model,
            tools=tools or [],
            system_prompt=load_prompt("prompts/itinerary-manage-agent-system.md"),
            middleware=middleware,
            checkpointer=checkpointer,
            name="itineraryManageAgent",
        )

    @classmethod
    def create_with_context(
        cls,
        model: BaseChatModel,
        settings: Settings,
        context: AgentContext,
        *,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        summary_model: BaseChatModel | None = None,
    ) -> ItineraryManageAgent:
        """按一次调用上下文创建带七类工具的行程管理 Agent。"""
        client = TravelManageApiClient(
            settings.api_server_base_url, settings.api_agent_token_file, context
        )
        tool_groups = (
            TravelOrderWriteTools(client),
            TravelOrderConflictTools(client),
            TravelOrderReadTools(client),
            BookingReadTools(client),
            BookingWriteTools(client),
            PolicyTools(client),
            QueryUserInfoTools(client),
        )
        tools = [tool for group in tool_groups for tool in group.as_tools()]
        return cls(
            model,
            settings,
            context,
            checkpointer=checkpointer,
            tools=tools,
            summary_model=summary_model,
        )

    async def ainvoke(self, message: str, session_id: str | None = None) -> Any:
        """运行行程管理 Agent，使用稳定 session_id 以支持未来 HITL 恢复。"""
        token = bind_session_context(self.session_context)
        try:
            return await self.graph.ainvoke(
                {"messages": [{"role": "user", "content": message}]},
                config={"configurable": {"thread_id": session_id}} if session_id else None,
            )
        finally:
            reset_session_context(token)

    async def aresume(self, resume_value: dict[str, Any], session_id: str) -> Any:
        """以子 Agent 派生线程恢复 HumanInTheLoopMiddleware 暂停。"""
        token = bind_session_context(self.session_context)
        try:
            return await self.graph.ainvoke(
                Command(resume=resume_value),
                config={"configurable": {"thread_id": session_id}},
            )
        finally:
            reset_session_context(token)
