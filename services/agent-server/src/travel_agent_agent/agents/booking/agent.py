# 文件职责：提供只读预订子 Agent 骨架，复用 BaseSubAgent 的差旅单查询工具。
# 定义 BookingAgent 及其 LangGraph 创建和调用方法，不注册预订写工具。
from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.agents.base_sub_agent import BaseSubAgent
from travel_agent_agent.agents.common.session_context import (
    bind_session_context,
    reset_session_context,
)
from travel_agent_agent.agents.itinerary_manage.tools import BookingReadTools, QueryUserInfoTools
from travel_agent_agent.core.prompt_loader import load_prompt
from travel_agent_agent.core.settings import Settings


class BookingAgent(BaseSubAgent):
    """只读预订骨架，不能执行取消、支付或外部平台写操作。"""

    def __init__(
        self,
        model: BaseChatModel,
        settings: Settings,
        context: AgentContext,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        """装配差旅单查询和内部预订记录查询工具。"""
        super().__init__(settings, context)
        self.booking_read_tools = BookingReadTools(self.client)
        self.profile_tools = QueryUserInfoTools(self.client)
        self.graph = create_agent(
            model=model,
            tools=[
                *self.shared_read_tools(),
                *self.booking_read_tools.as_tools(),
                *self.profile_tools.as_tools(),
            ],
            system_prompt=load_prompt("prompts/itinerary-manage-agent-system.md"),
            checkpointer=checkpointer,
            name="bookingAgent",
        )

    async def ainvoke(self, message: str, session_id: str | None = None) -> Any:
        """执行一次只读预订查询请求。"""
        token = bind_session_context(self.session_context)
        try:
            return await self.graph.ainvoke(
                {"messages": [{"role": "user", "content": message}]},
                config={"configurable": {"thread_id": session_id}} if session_id else None,
            )
        finally:
            reset_session_context(token)
