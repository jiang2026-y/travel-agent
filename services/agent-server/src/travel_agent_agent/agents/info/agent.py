# 文件职责：实现基于 glm-5.1 的双通道信息查询 ReAct Agent。
# 定义 InfoAgent，负责知识库检索、政策工具查询、目的地实时查询与降级处理。
from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.language_models import BaseChatModel

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
from travel_agent_agent.agents.info.tools import (
    DestinationLiveTools,
    KnowledgeRetriever,
    KnowledgeRetrieveTools,
)
from travel_agent_agent.agents.info.visa_tools import VisaTools
from travel_agent_agent.agents.itinerary_manage.time_hook import DynamicTimeInjectionHook
from travel_agent_agent.agents.itinerary_manage.tools import PolicyTools
from travel_agent_agent.agents.master.summary import build_summary_middleware
from travel_agent_agent.core.prompt_loader import load_prompt
from travel_agent_agent.core.settings import Settings
from travel_agent_agent.infrastructure.destination_live_client import DestinationLiveClient
from travel_agent_agent.infrastructure.visa_client import VisaClient


class InfoAgent(BaseSubAgent):
    """查询政策、景点与公共出行信息的只读智能体，不执行任何写操作。"""

    name = "infoAgent"
    model_name = "glm-5.1"
    max_iters = 5

    def __init__(
        self,
        model: BaseChatModel,
        settings: Settings,
        context: AgentContext,
        knowledge: KnowledgeRetriever,
        *,
        destination_client: DestinationLiveClient | None = None,
        visa_client: VisaClient | None = None,
        summary_model: BaseChatModel | None = None,
    ) -> None:
        """装配知识库检索、政策工具与目的地实时查询工具。"""
        super().__init__(settings, context)
        self.knowledge_tools = KnowledgeRetrieveTools(knowledge, context)
        self.policy_tools = PolicyTools(self.client)
        self.destination_tools = (
            DestinationLiveTools(destination_client) if destination_client is not None else None
        )
        tools: list[Any] = [
            *self.knowledge_tools.as_tools(),
            *self.policy_tools.as_tools(),
        ]
        if self.destination_tools is not None:
            tools.extend(self.destination_tools.as_tools())
        if visa_client is not None:
            tools.extend(VisaTools(visa_client).as_tools())
        # 天气与行程咨询依赖相对日期（如"明天"），因此与行程管理、预订一致注入动态中国时间。
        middleware: list[Any] = [
            *configured_circuit_breaker_middleware(),
            *build_context_efficiency_middleware(),
            *build_tool_progress_middleware("infoAgent", lambda: self.context),
            DynamicTimeInjectionHook(),
        ]
        middleware.append(build_tool_error_middleware())
        summary_middleware = build_summary_middleware(summary_model)
        if summary_middleware is not None:
            middleware.append(summary_middleware)
        middleware.append(ModelCallLimitMiddleware(run_limit=self.max_iters, exit_behavior="end"))
        self.graph = create_agent(
            model=model,
            tools=tools,
            system_prompt=load_prompt("prompts/info-agent-system.md"),
            middleware=middleware,
            name="infoAgent",
        )

    async def ainvoke(self, message: str, session_id: str | None = None) -> Any:
        """执行一次只读信息查询，不写入任何业务数据。"""
        del session_id
        token = bind_session_context(self.session_context)
        try:
            return await self.graph.ainvoke({"messages": [{"role": "user", "content": message}]})
        finally:
            reset_session_context(token)
