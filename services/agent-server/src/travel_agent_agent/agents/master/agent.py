# 本文件实现基于 LangChain ReAct 的 MasterAgent 和意图结果自然语言简报。
# 定义 serialize_intent_result、MasterAgent 及四个默认子 Agent 配置工厂。
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command, interrupt

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.agents.booking.agent import BookingAgent
from travel_agent_agent.agents.itinerary_manage.agent import ItineraryManageAgent
from travel_agent_agent.agents.itinerary_plan.agent import ItineraryPlanAgent
from travel_agent_agent.agents.master.provider import (
    SubAgentProvider,
    SubAgentRequest,
    SubAgentResult,
    default_sub_agent_configs,
)
from travel_agent_agent.agents.master.tools import InfoQueryTools, UserInteractionTools
from travel_agent_agent.core.prompt_loader import load_prompt
from travel_agent_agent.core.settings import Settings
from travel_agent_agent.intent.result import IntentRecognitionResult


def serialize_intent_result(result: IntentRecognitionResult) -> str:
    """将结构化意图转换为传给 Master/子 Agent 的自然语言任务简报。"""
    intents = "、".join(item.intent for item in result.intents)
    return (
        f"当前识别为{'多意图' if result.multi_intent else '单意图'}。"
        f"主要诉求：{intents}。识别来源：{result.source.value}。"
        f"整体判断：{result.overall_reason}。"
    )


class MasterAgent:
    """封装 qwen3.7-plus 非思考 ReAct Agent，工具仅来自显式白名单。"""

    model_name = "qwen3.7-plus"
    enable_thinking = False

    @classmethod
    def create_with_default_provider(
        cls, model: BaseChatModel, checkpointer: BaseCheckpointSaver | None = None,
        settings: Settings | None = None,
    ) -> MasterAgent:
        """使用四个默认子 Agent 配置创建 Master；配置注册不实例化子 Agent。"""
        configs = list(default_sub_agent_configs())
        if settings is not None:
            for index, config in enumerate(configs):
                if config.key not in {
                    "itinerary_manage_agent",
                    "itinerary_plan_agent",
                    "booking_agent",
                }:
                    continue

                def factory(config_key: str = config.key) -> Any:
                    agent_holder: dict[tuple[str, str], Any] = {}

                    async def handler(request: SubAgentRequest) -> SubAgentResult:
                        agent_key = (request.context.user_id, request.context.thread_id)
                        agent = agent_holder.get(agent_key)
                        if agent is None:
                            if config_key == "itinerary_manage_agent":
                                agent = ItineraryManageAgent.create_with_context(
                                    model,
                                    settings,
                                    request.context,
                                    checkpointer=checkpointer,
                                )
                            elif config_key == "itinerary_plan_agent":
                                agent = ItineraryPlanAgent(
                                    model,
                                    settings,
                                    request.context,
                                    checkpointer=checkpointer,
                                )
                            else:
                                agent = BookingAgent(
                                    model,
                                    settings,
                                    request.context,
                                    checkpointer=checkpointer,
                                )
                            agent_holder[agent_key] = agent
                        if request.resume_value is not None and hasattr(agent, "aresume"):
                            result = await agent.aresume(
                                request.resume_value, request.session_id or agent_key[1]
                            )
                        else:
                            result = await agent.ainvoke(request.message, request.session_id)
                        messages = result.get("messages", []) if isinstance(result, dict) else []
                        pending = cls._extract_pending_interaction(result)
                        content = (
                            messages[-1].content if messages else "行程管理 Agent 未返回结果。"
                        )
                        return SubAgentResult(
                            str(content), request.session_id, pending_interaction=pending
                        )

                    return handler

                configs[index] = type(config)(config.key, factory, True)
        return cls(model, SubAgentProvider(tuple(configs)), checkpointer=checkpointer)

    @staticmethod
    def _extract_pending_interaction(result: object) -> dict[str, object] | None:
        """从子 Agent LangGraph 输出提取中断摘要，交回父图统一签发 Token。"""
        if not isinstance(result, dict):
            return None
        interrupts = result.get("__interrupt__")
        if not isinstance(interrupts, (list, tuple)) or not interrupts:
            return None
        value = getattr(interrupts[0], "value", interrupts[0])
        if not isinstance(value, dict):
            return None
        if value.get("kind"):
            return value
        actions = value.get("action_requests")
        if not isinstance(actions, list) or not actions:
            return None
        action = actions[0]
        if not isinstance(action, dict):
            return None
        return {
            "kind": "approval",
            "action": {"tool_name": action.get("name"), "tool_args": action.get("args")},
        }

    def __init__(
        self,
        model: BaseChatModel,
        provider: SubAgentProvider,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        """注入 LangChain 聊天模型和懒加载子 Agent Provider。"""
        self.provider = provider
        self._context: ContextVar[AgentContext | None] = ContextVar(
            "master_agent_context", default=None
        )
        self._resume_value: ContextVar[dict[str, Any] | None] = ContextVar(
            "master_agent_resume_value", default=None
        )
        info = InfoQueryTools()
        interaction = UserInteractionTools()

        async def query_info(question: str) -> str:
            """调用通用只读信息工具。"""
            return await info.query(question)

        async def ask_user(question: str, ui_type: str = "text") -> str:
            """构造主动提问结果，等待上层转为中断事件。"""
            question_event = await interaction.ask_user(question, ui_type)
            return question_event.question

        async def call_sub_agent(
            agent_key: str, message: str, session_id: str | None = None
        ) -> str:
            """以自然语言简报调用已登记子 Agent，拒绝未注册 Agent。"""
            active_context = self._context.get() or AgentContext(trace_id="master-agent")
            request = SubAgentRequest(
                message=message,
                session_id=session_id or f"{active_context.thread_id}:{agent_key}",
                context=active_context,
                resume_value=self._resume_value.get(),
            )
            result = await provider.invoke(agent_key, request)
            if result.pending_interaction is not None:
                interrupt(result.pending_interaction)
            return result.content

        tools = [
            StructuredTool.from_function(
                coroutine=query_info,
                name="info_query",
                description="查询通用只读旅行信息，不执行任何写操作。",
            ),
            StructuredTool.from_function(
                coroutine=ask_user,
                name="ask_user",
                description="向用户提出必要的澄清问题。",
            ),
            StructuredTool.from_function(
                coroutine=call_sub_agent,
                name="call_sub_agent",
                description="调用已登记的四个旅行子智能体。",
            ),
        ]
        self.graph = create_agent(
            model=model,
            tools=tools,
            system_prompt=load_prompt("prompts/masteragent-system.md"),
            name="masterAgent",
            checkpointer=checkpointer,
        )

    async def ainvoke(
        self,
        message: str,
        rewritten_question: str,
        intent_result_json: str,
        session_id: str | None = None,
        context: AgentContext | None = None,
    ) -> Any:
        """调用 Master ReAct 图，只消费上游改写问题和意图 JSON。"""
        token: Token[AgentContext | None] = self._context.set(
            context or AgentContext(trace_id="master-agent")
        )
        try:
            return await self.graph.ainvoke(
                {"messages": [
                    {"role": "system", "content": f"改写后的问题：{rewritten_question}"},
                    {"role": "system", "content": f"意图识别 JSON：{intent_result_json}"},
                    {"role": "user", "content": message},
                ]},
                config={"configurable": {"thread_id": session_id}} if session_id else None,
            )
        finally:
            self._context.reset(token)

    async def aresume(
        self,
        resume_value: dict[str, Any],
        thread_id: str,
        context: AgentContext,
    ) -> Any:
        """以稳定主线程恢复显式中断或子 Agent 转发的 HITL 决策。"""
        token: Token[AgentContext | None] = self._context.set(context)
        resume_token = self._resume_value.set(resume_value)
        try:
            return await self.graph.ainvoke(
                Command(resume=resume_value),
                config={"configurable": {"thread_id": thread_id}},
            )
        finally:
            self._context.reset(token)
            self._resume_value.reset(resume_token)
