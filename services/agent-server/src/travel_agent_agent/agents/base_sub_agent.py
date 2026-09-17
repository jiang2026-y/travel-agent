# 文件职责：提供 ItineraryManageAgent、ItineraryPlanAgent 和 BookingAgent 共用的子 Agent 基类。
# 定义 BaseSubAgent，负责上下文、内部客户端和共享 TravelOrderReadTools 装配。
from __future__ import annotations

from langchain_core.tools import StructuredTool

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.agents.common.session_context import SessionCtx
from travel_agent_agent.agents.common.travel_order_read_tools import TravelOrderReadTools
from travel_agent_agent.agents.itinerary_manage.client import TravelManageApiClient
from travel_agent_agent.core.settings import Settings


class BaseSubAgent:
    """提供所有业务子 Agent 的只读差旅能力与请求上下文。"""

    def __init__(self, settings: Settings, context: AgentContext) -> None:
        """创建当前请求专属客户端和 SessionCtx，不共享用户状态。"""
        self.context = context
        self.session_context = SessionCtx(
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            run_id=context.run_id,
            thread_id=context.thread_id,
        )
        self.client = TravelManageApiClient(
            settings.api_server_base_url, settings.api_agent_token_file, context
        )
        self.travel_order_read_tools = TravelOrderReadTools(self.client)

    def shared_read_tools(self) -> list[StructuredTool]:
        """返回共享的单一差旅单查询工具。"""
        return list(self.travel_order_read_tools.as_tools())
