# 文件职责：提供只读行程规划子 Agent 骨架，复用 BaseSubAgent 的差旅单查询工具。
# 定义 ItineraryPlanAgent 及其 LangGraph 创建和调用方法。
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
from travel_agent_agent.agents.itinerary_manage.tools import QueryUserInfoTools
from travel_agent_agent.core.prompt_loader import load_prompt
from travel_agent_agent.core.settings import Settings


class ItineraryPlanAgent(BaseSubAgent):
    """只读收集行程条件并查询差旅单，不执行任何业务写入。"""

    def __init__(
        self,
        model: BaseChatModel,
        settings: Settings,
        context: AgentContext,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        """装配共享只读工具和规划提示词。"""
        super().__init__(settings, context)
        self.profile_tools = QueryUserInfoTools(self.client)
        self.graph = create_agent(
            model=model,
            tools=[*self.shared_read_tools(), *self.profile_tools.base_location_tools()],
            system_prompt=load_prompt("prompts/itinerary-manage-agent-system.md"),
            checkpointer=checkpointer,
            name="itineraryPlanAgent",
        )

    async def ainvoke(self, message: str, session_id: str | None = None) -> Any:
        """执行一次只读规划请求。"""
        token = bind_session_context(self.session_context)
        try:
            return await self.graph.ainvoke(
                {"messages": [{"role": "user", "content": message}]},
                config={"configurable": {"thread_id": session_id}} if session_id else None,
            )
        finally:
            reset_session_context(token)
