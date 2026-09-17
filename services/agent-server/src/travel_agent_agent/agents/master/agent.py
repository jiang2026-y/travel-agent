# 本文件实现基于 LangChain ReAct 的 MasterAgent 与意图结果自然语言简报。
# 定义 serialize_intent_result、serialize_intent_result_json、MasterAgent 及按名注册的子智能体工具。
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextvars import ContextVar, Token
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command, interrupt

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.agents.booking.agent import BookingAgent
from travel_agent_agent.agents.common.circuit_breaker import (
    configured_circuit_breaker_middleware,
)
from travel_agent_agent.agents.common.context_efficiency import (
    build_context_efficiency_middleware,
)
from travel_agent_agent.agents.common.tool_errors import build_tool_error_middleware
from travel_agent_agent.agents.common.tool_progress import build_tool_progress_middleware
from travel_agent_agent.agents.info.agent import InfoAgent
from travel_agent_agent.agents.itinerary_manage.agent import ItineraryManageAgent
from travel_agent_agent.agents.master.memory_tools import PreferenceMemoryTools
from travel_agent_agent.agents.master.provider import (
    SubAgentConfig,
    SubAgentProvider,
    SubAgentRequest,
    SubAgentResult,
    default_sub_agent_configs,
)
from travel_agent_agent.agents.master.summary import build_summary_middleware
from travel_agent_agent.agents.master.tools import UserInteractionTools
from travel_agent_agent.core.prompt_loader import load_prompt
from travel_agent_agent.core.settings import Settings
from travel_agent_agent.infrastructure.dashscope_client import create_bailian_knowledge_client
from travel_agent_agent.infrastructure.destination_live_client import DestinationLiveClient
from travel_agent_agent.infrastructure.memory_client import BailianMemoryClient
from travel_agent_agent.infrastructure.visa_client import VisaClient
from travel_agent_agent.intent.result import IntentRecognitionResult

DEFAULT_MAX_ITERATIONS = 15
IMPLEMENTED_SUB_AGENT_KEYS = (
    "itinerary_manage_agent",
    "booking_agent",
    "info_agent",
)


def serialize_intent_result(result: IntentRecognitionResult) -> str:
    """将结构化意图转换为传给 Master/子 Agent 的自然语言任务简报。"""
    intents = "、".join(item.intent for item in result.intents)
    return (
        f"当前识别为{'多意图' if result.multi_intent else '单意图'}。"
        f"主要诉求：{intents}。识别来源：{result.source.value}。"
        f"整体判断：{result.overall_reason}。"
    )


def serialize_intent_result_json(result: IntentRecognitionResult) -> str:
    """将意图结果压缩为标题生成可用的最小 JSON，不包含用户敏感正文。"""
    payload = {
        "primary_intent": result.intents[0].intent if result.intents else "",
        "intents": [item.intent for item in result.intents],
        "multi_intent": result.multi_intent,
        "source": result.source.value,
    }
    return json.dumps(payload, ensure_ascii=False)


class MasterAgent:
    """封装 qwen3.7-plus 非思考 ReAct Agent，工具仅来自显式白名单。"""

    model_name = "qwen3.7-plus"
    enable_thinking = False
    max_iters = DEFAULT_MAX_ITERATIONS

    @classmethod
    def create_with_default_provider(
        cls,
        model: BaseChatModel,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        settings: Settings | None = None,
        info_model: BaseChatModel | None = None,
        sub_agent_models: dict[str, BaseChatModel] | None = None,
        summary_model: BaseChatModel | None = None,
        destination_client: DestinationLiveClient | None = None,
        visa_client: VisaClient | None = None,
        memory_client: BailianMemoryClient | None = None,
    ) -> MasterAgent:
        """使用默认子 Agent 配置创建 Master；配置注册不在启动阶段实例化子 Agent。"""
        configs = list(default_sub_agent_configs())
        if settings is not None:
            models = sub_agent_models or {}
            for index, config in enumerate(configs):
                if not config.enabled or config.key not in IMPLEMENTED_SUB_AGENT_KEYS:
                    continue
                default_model = info_model if config.key == "info_agent" else model
                agent_model = models.get(config.key, default_model)
                if agent_model is None:
                    continue
                factory = cls._build_sub_agent_factory(
                    config.key,
                    agent_model,
                    settings,
                    checkpointer,
                    summary_model,
                    destination_client,
                    memory_client,
                    visa_client,
                )
                configs[index] = type(config)(
                    config.key, factory, True, config.description
                )
        return cls(
            model,
            SubAgentProvider(tuple(configs)),
            checkpointer=checkpointer,
            summary_model=summary_model,
            memory_client=memory_client,
        )

    @classmethod
    def _build_sub_agent_factory(
        cls,
        config_key: str,
        model: BaseChatModel,
        settings: Settings,
        checkpointer: BaseCheckpointSaver[Any] | None,
        summary_model: BaseChatModel | None = None,
        destination_client: DestinationLiveClient | None = None,
        memory_client: BailianMemoryClient | None = None,
        visa_client: VisaClient | None = None,
    ) -> Any:
        """构建按用户与线程缓存子 Agent 实例的懒加载工厂。"""

        def factory() -> Any:
            agent_holder: dict[tuple[str, str], Any] = {}

            async def handler(request: SubAgentRequest) -> SubAgentResult:
                agent_key = (request.context.user_id, request.context.thread_id)
                agent = agent_holder.get(agent_key)
                if agent is None:
                    agent = cls._create_sub_agent(
                        config_key,
                        model,
                        settings,
                        request,
                        checkpointer,
                        summary_model,
                        destination_client,
                        memory_client,
                        visa_client,
                    )
                    agent_holder[agent_key] = agent
                session_id = request.session_id or agent_key[1]
                # 只有当该子智能体确实停在中断上时才用恢复值续跑；
                # 否则会把上一轮的恢复值误当成它的恢复指令，导致它重跑并重复提问。
                if (
                    request.resume_value is not None
                    and hasattr(agent, "aresume")
                    and await _has_pending_interrupt(agent, session_id)
                ):
                    result = await agent.aresume(
                        request.resume_value, session_id
                    )
                else:
                    result = await agent.ainvoke(request.message, request.session_id)
                messages = result.get("messages", []) if isinstance(result, dict) else []
                pending = cls._extract_pending_interaction(result)
                content = _extract_sub_agent_content(messages)
                return SubAgentResult(
                    str(content), request.session_id, pending_interaction=pending
                )

            return handler

        return factory

    @staticmethod
    def _create_sub_agent(
        config_key: str,
        model: BaseChatModel,
        settings: Settings,
        request: SubAgentRequest,
        checkpointer: BaseCheckpointSaver[Any] | None,
        summary_model: BaseChatModel | None = None,
        destination_client: DestinationLiveClient | None = None,
        memory_client: BailianMemoryClient | None = None,
        visa_client: VisaClient | None = None,
    ) -> Any:
        """按配置键创建具体子 Agent，未实现的键一律拒绝。"""
        if config_key == "itinerary_manage_agent":
            return ItineraryManageAgent.create_with_context(
                model,
                settings,
                request.context,
                checkpointer=checkpointer,
                summary_model=summary_model,
            )
        if config_key == "booking_agent":
            return BookingAgent(
                model,
                settings,
                request.context,
                checkpointer=checkpointer,
                summary_model=summary_model,
                memory_client=memory_client,
            )
        if config_key == "info_agent":
            return InfoAgent(
                model,
                settings,
                request.context,
                create_bailian_knowledge_client(settings),
                destination_client=destination_client,
                summary_model=summary_model,
                visa_client=visa_client,
            )
        raise ValueError("sub_agent_not_implemented")

    @staticmethod
    def _extract_pending_interaction(result: object) -> dict[str, object] | None:
        """从子 Agent LangGraph 输出提取中断摘要，交由父图统一签发 Token。"""
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
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        summary_model: BaseChatModel | None = None,
        memory_client: BailianMemoryClient | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ) -> None:
        """注入模型、懒加载子 Agent Provider、摘要模型与长期记忆工具。"""
        self.provider = provider
        self._context: ContextVar[AgentContext | None] = ContextVar(
            "master_agent_context", default=None
        )
        self._resume_value: ContextVar[dict[str, Any] | None] = ContextVar(
            "master_agent_resume_value", default=None
        )
        interaction = UserInteractionTools()

        async def ask_user(
            question: str,
            ui_type: str = "text",
            options: list[str] | None = None,
            fields: list[dict[str, Any]] | None = None,
            default_value: str | None = None,
            allow_other: bool = False,
        ) -> str:
            """向用户提出结构化问题，暂停等待交互结果。"""
            return await interaction.ask_user(
                question,
                ui_type,
                tuple(options or ()),
                tuple(fields or ()),
                default_value,
                allow_other,
            )

        tools: list[StructuredTool] = [
            StructuredTool.from_function(
                coroutine=ask_user,
                name="ask_user",
                description=(
                    "当请求缺信息、有歧义或需要用户选择时向用户提问。"
                    "ui_type 可选 text/select/multi_select/confirm/form/date/number；"
                    "select 与 multi_select 需提供 options，form 需提供 fields。"
                ),
            )
        ]
        for config in provider.enabled_configs:
            tools.append(self._sub_agent_tool(config))
        if memory_client is not None:
            tools.extend(
                PreferenceMemoryTools(memory_client, lambda: self._context.get()).as_tools()
            )
        middleware: list[Any] = [
            *configured_circuit_breaker_middleware(),
            *build_context_efficiency_middleware(),
            *build_tool_progress_middleware("masterAgent", lambda: self._context.get()),
        ]
        middleware.append(build_tool_error_middleware())
        summary_middleware = build_summary_middleware(summary_model)
        if summary_middleware is not None:
            middleware.append(summary_middleware)
        middleware.append(
            ModelCallLimitMiddleware(run_limit=max_iterations, exit_behavior="end")
        )
        self.graph = create_agent(
            model=model,
            tools=tools,
            system_prompt=load_prompt("prompts/masteragent-system.md"),
            middleware=middleware,
            name="masterAgent",
            checkpointer=checkpointer,
        )

    def _sub_agent_tool(self, config: SubAgentConfig) -> StructuredTool:
        """按 Java 约定把每个子 Agent 注册成一个同名工具。"""
        agent_key = config.key

        async def call_sub_agent(message: str, session_id: str | None = None) -> str:
            """以自然语言任务调用指定子 Agent，并按需向上层转交中断。"""
            active_context = self._context.get() or AgentContext(trace_id="master-agent")
            request = SubAgentRequest(
                message=message,
                session_id=session_id or f"{active_context.thread_id}:{agent_key}",
                context=active_context,
                resume_value=self._resume_value.get(),
            )
            result = await self.provider.invoke(agent_key, request)
            if result.pending_interaction is not None:
                interrupt(result.pending_interaction)
            return result.content

        return StructuredTool.from_function(
            coroutine=call_sub_agent, name=agent_key, description=config.tool_description
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
                {
                    "messages": [
                        {"role": "system", "content": f"改写后的问题：{rewritten_question}"},
                        {"role": "system", "content": f"意图识别 JSON：{intent_result_json}"},
                        {"role": "user", "content": message},
                    ]
                },
                config={"configurable": {"thread_id": session_id}} if session_id else None,
            )
        finally:
            self._context.reset(token)

    async def astream(
        self,
        message: str,
        rewritten_question: str,
        intent_result_json: str,
        session_id: str | None = None,
        context: AgentContext | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """流式执行 Master 图：先逐段产出最终回答文本，最后产出完整状态。

        只有顶层模型节点的正文分片会被产出；工具调用分片与子智能体内部推理
        （默认不进入父图事件流）都会被过滤，避免前端看到重复或内部内容。
        """
        token: Token[AgentContext | None] = self._context.set(
            context or AgentContext(trace_id="master-agent")
        )
        try:
            final: Any = None
            async for mode, chunk in self.graph.astream(
                {
                    "messages": [
                        {"role": "system", "content": f"改写后的问题：{rewritten_question}"},
                        {"role": "system", "content": f"意图识别 JSON：{intent_result_json}"},
                        {"role": "user", "content": message},
                    ]
                },
                config={"configurable": {"thread_id": session_id}} if session_id else None,
                stream_mode=["messages", "values"],
            ):
                if mode == "messages":
                    text = _streamed_text(chunk)
                    if text:
                        yield ("delta", text)
                    continue
                final = chunk
            yield ("final", final)
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


async def _has_pending_interrupt(agent: Any, session_id: str) -> bool:
    """判断子智能体当前是否真的停在中断上，避免误用恢复值重跑。"""
    graph = getattr(agent, "graph", None)
    if graph is None or not hasattr(graph, "aget_state"):
        return False
    try:
        state = await graph.aget_state({"configurable": {"thread_id": session_id}})
    except Exception:
        return False
    for task in getattr(state, "tasks", ()) or ():
        if getattr(task, "interrupts", None):
            return True
    return False


def _streamed_text(chunk: object) -> str:
    """从顶层模型节点的流式分片中提取正文，跳过工具调用分片与空文本。"""
    message, metadata = chunk if isinstance(chunk, tuple) and len(chunk) == 2 else (chunk, {})
    if isinstance(metadata, dict) and metadata.get("langgraph_node") != "model":
        return ""
    if getattr(message, "tool_call_chunks", None):
        return ""
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else ""


def _extract_sub_agent_content(messages: object) -> str:
    """兼容 LangChain 消息对象和 InfoAgent 返回的安全字典消息。"""
    if not isinstance(messages, (list, tuple)) or not messages:
        return "子 Agent 未返回结果。"
    message = messages[-1]
    value = (
        message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    )
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        text = "".join(
            item.get("text", "")
            for item in value
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ).strip()
        if text:
            return text
    return "子 Agent 未返回可展示结果。"
