# 本文件定义 API Server 调用 Agent Server 的受保护内部命令边界。
# 定义内部用户上下文、停止记忆命令和结果。
# 定义 Docker Secret 认证、路由注册和二次身份校验函数。
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from travel_agent_sensitive_masker import SensitiveMasker

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.agents.common.context_efficiency import (
    bind_token_usage,
    reset_token_usage,
)
from travel_agent_agent.agents.intent_recognition import IntentRecognitionAgent
from travel_agent_agent.agents.itinerary_manage.hitl_context import (
    ToolExecutionAuthorization,
    bind_tool_execution_authorization,
    reset_tool_execution_authorization,
)
from travel_agent_agent.agents.master.agent import MasterAgent
from travel_agent_agent.api.errors import ServiceError
from travel_agent_agent.core.correlation import get_correlation_context
from travel_agent_agent.core.settings import Settings
from travel_agent_agent.intent.result import (
    IntentConfidence,
    IntentRecognitionResult,
    IntentSource,
    RecognizedIntent,
)
from travel_agent_agent.orchestration.checkpoints import (
    CheckpointError,
    RunCheckpoint,
    RunCheckpointStore,
)
from travel_agent_agent.orchestration.execution import (
    RunExecution,
    RunExecutionRegistry,
    RunInterruptCoordinator,
)
from travel_agent_agent.orchestration.interactions import (
    InteractionError,
    PendingInteraction,
    PendingInteractionStore,
    canonical_args_hash,
)
from travel_agent_agent.orchestration.routing import RouteAction, RunRoute, route_from_recognition
from travel_agent_agent.orchestration.status import RunStatus
from travel_agent_agent.preferences.agent import PreferenceParseAgent
from travel_agent_agent.recommendation.events import (
    RecommendationEventError,
    RecommendationEventStore,
)
from travel_agent_agent.recommendation.signals import continuation_signals
from travel_agent_agent.title.agent import ConversationTitleAgent

_ROUTE_PROVIDER_KEYS = {
    "itineraryManageAgent": "itinerary_manage_agent",
    "bookingAgent": "booking_agent",
    "infoAgent": "info_agent",
}
_PROVIDER_AGENT_NAMES = {value: key for key, value in _ROUTE_PROVIDER_KEYS.items()}

_USER_ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"
_MINIMUM_TOKEN_LENGTH = 32
_SUSPENDABLE_PRIVACY_STATES = frozenset({"deletion_pending", "deleted"})
_TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED}
)
# 流式回答按字符数合并发布，兼顾实时性与事件存储写入量。
_TOKEN_FLUSH_CHARS = 24
_LOGGER = logging.getLogger("travel_agent_agent.internal_commands")


class InternalUserContext(BaseModel):
    """表示 API Server 已完成会话校验后传给 Agent 的最小用户安全上下文。"""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(pattern=_USER_ID_PATTERN)
    role: Literal["user", "admin"]
    privacy_status: Literal["active", "deletion_pending", "deleted"]


class SuspendUserMemoryCommand(BaseModel):
    """表示注销或删除受理后要求 Agent 停止长期记忆注入的内部命令。"""

    model_config = ConfigDict(extra="forbid")

    command_version: Literal["v1"] = "v1"
    command: Literal["suspend_user_memory"]
    request_id: str = Field(pattern=_USER_ID_PATTERN)
    trace_id: str = Field(pattern=_USER_ID_PATTERN)
    user: InternalUserContext
    reason: Literal["account_deletion_requested", "account_deleted"]


class SuspendUserMemoryResult(BaseModel):
    """表示 Agent 已接受停止记忆命令；不代表物理删除已经完成。"""

    command: Literal["suspend_user_memory"] = "suspend_user_memory"
    status: Literal["memory_suspended"] = "memory_suspended"
    user_id: str
    privacy_status: Literal["deletion_pending", "deleted"]


class _RunCommandBase(BaseModel):
    """定义运行级内部命令共享的身份、关联标识与会话边界字段。"""

    model_config = ConfigDict(extra="forbid")

    command_version: Literal["v1"] = "v1"
    request_id: str = Field(pattern=_USER_ID_PATTERN)
    trace_id: str = Field(pattern=_USER_ID_PATTERN)
    user: InternalUserContext
    conversation_id: str = Field(pattern=_USER_ID_PATTERN)
    run_id: str = Field(pattern=_USER_ID_PATTERN)
    thread_id: str = Field(pattern=_USER_ID_PATTERN)


class StartRunCommand(_RunCommandBase):
    """表示 API 已持久化用户消息后，请求 Agent 创建可恢复运行的内部命令。"""

    command: Literal["start"]
    task_brief: str = Field(min_length=1, max_length=8000)
    context_summary: str = Field(max_length=2000)


class ResumeRunCommand(_RunCommandBase):
    """表示用户补充消息后，请求 Agent 从同一检查点继续运行的内部命令。"""

    command: Literal["resume"]
    task_brief: str = Field(min_length=1, max_length=8000)
    context_summary: str = Field(max_length=2000)
    resume_decision: ResumeDecision | None = None
    quick_action: str | None = Field(default=None, max_length=32)


class ResumeDecision(BaseModel):
    """表示前端对待澄清或待确认交互提交的结构化恢复决定。"""

    model_config = ConfigDict(extra="forbid")

    interaction_id: str = Field(pattern=_USER_ID_PATTERN)
    decision: Literal["approve", "reject", "edit", "respond"]
    message: str | None = Field(default=None, max_length=8000)
    edited_args: dict[str, Any] | None = None


class ConsumeHITLTokenCommand(_RunCommandBase):
    """表示 API Server 在写事务前请求原子消费确认 Token 的内部命令。"""

    command: Literal["consume_hitl_token"] = "consume_hitl_token"
    interaction_id: str = Field(pattern=_USER_ID_PATTERN)
    tool_name: str = Field(pattern=_USER_ID_PATTERN)
    tool_args: dict[str, Any]
    decision: Literal["approve"] = "approve"


class HITLTokenConsumeResult(BaseModel):
    """返回可作为写操作幂等键的已消费交互标识。"""

    interaction_id: str
    idempotency_key: str


class CancelRunCommand(_RunCommandBase):
    """表示用户主动中止指定 Run 的内部命令，不包含原始任务正文。"""

    command: Literal["cancel"]


class RecommendationCommand(_RunCommandBase):
    """表示主答案完成后异步请求推荐问题的内部命令。"""

    command: Literal["recommendations"]
    user_question: str = Field(min_length=1, max_length=8000)
    assistant_reply: str = Field(min_length=1, max_length=16000)
    context_summary: str = Field(default="", max_length=2000)
    answer_version: str = Field(pattern=_USER_ID_PATTERN)
    run_status: Literal[
        "proposed",
        "running",
        "clarifying",
        "awaiting_approval",
        "completed",
        "failed",
        "cancelled",
    ]
    has_pending_interaction: bool = False


class ConversationTitleCommand(_RunCommandBase):
    """表示意图识别完成后异步请求会话标题的内部命令。"""

    command: Literal["conversation_title"]
    user_question: str = Field(min_length=1, max_length=8000)
    intent_result_json: str = Field(default="", max_length=2000)


class DebugAgentCommand(_RunCommandBase):
    """表示管理员调试直达某个子智能体的内部命令。"""

    command: Literal["debug_agent"]
    message: str = Field(min_length=1, max_length=8000)
    context_summary: str = Field(default="", max_length=2000)


class MemoryRecordRequest(BaseModel):
    """约束偏好写入请求，只接受有限长度的脱敏偏好句子。"""

    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=2000)


class MemoryRetrieveRequest(BaseModel):
    """约束偏好召回请求，只接受有限长度的查询文本。"""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=1000)


class PreferenceParseRequest(BaseModel):
    """约束偏好解析请求：记忆原文与目录（key → 允许取值）。"""

    model_config = ConfigDict(extra="forbid")
    memory_text: str = Field(min_length=1, max_length=8000)
    catalog: dict[str, list[str]] = Field(min_length=1, max_length=64)


class RunCommandResult(BaseModel):
    """返回检查点已接受的运行状态、版本及全部运行关联标识。"""

    model_config = ConfigDict(extra="forbid")

    command: Literal["start", "resume", "cancel"]
    status: str
    conversation_id: str
    run_id: str
    thread_id: str
    version: int
    routing_action: Literal["direct_dispatch", "clarify"] | None = None
    target_agent: str | None = None
    intent_code: str | None = None
    intent_source: str | None = None
    diagnostics: dict[str, object] = Field(default_factory=dict)
    pending_interaction: dict[str, Any] | None = None
    assistant_reply: str | None = None


async def require_internal_service(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """校验 Bearer Token 与 Docker Secret 一致，拒绝任何浏览器或未授权服务调用。"""
    settings = cast(Settings, request.app.state.settings)
    expected_token = _read_internal_token(settings.api_agent_token_file)
    if authorization is None or not authorization.startswith("Bearer "):
        raise ServiceError("internal_service_auth_failed", "内部服务认证失败", False, 401)
    presented_token = authorization.removeprefix("Bearer ")
    if not secrets.compare_digest(presented_token, expected_token):
        raise ServiceError("internal_service_auth_failed", "内部服务认证失败", False, 401)


def register_internal_command_routes(application: FastAPI | APIRouter) -> None:
    """注册仅供 API Server 调用的停止用户记忆命令，执行二次用户上下文校验。"""

    @application.post(
        "/internal/v1/commands/suspend-user-memory",
        response_model=SuspendUserMemoryResult,
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def suspend_user_memory(
        request: Request,
        command: SuspendUserMemoryCommand,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> SuspendUserMemoryResult:
        """只在注销或删除已受理时确认停止记忆，不执行物理删除或创建业务数据。"""
        _validate_internal_user_headers(
            command.user,
            x_internal_user_id,
            x_internal_user_role,
            x_internal_privacy_status,
        )
        context = get_correlation_context(request)
        if (command.request_id, command.trace_id) != (context.request_id, context.trace_id):
            raise ServiceError(
                "internal_correlation_mismatch",
                "内部调用链标识校验失败",
                False,
                403,
            )
        if command.user.privacy_status not in _SUSPENDABLE_PRIVACY_STATES:
            raise ServiceError(
                "privacy_state_not_suspendable",
                "当前隐私状态不允许停止记忆",
                False,
                409,
            )
        return SuspendUserMemoryResult(
            user_id=command.user.user_id,
            privacy_status=cast(
                Literal["deletion_pending", "deleted"], command.user.privacy_status
            ),
        )

    @application.post(
        "/internal/v1/recommendations",
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def generate_recommendations(
        request: Request,
        command: RecommendationCommand,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        """异步生成并发布推荐；跳过条件和模型失败均不影响主 Run。"""
        _validate_run_command(
            request,
            command,
            x_internal_user_id,
            x_internal_user_role,
            x_internal_privacy_status,
        )
        if (
            command.run_status in {"clarifying", "awaiting_approval", "failed", "cancelled"}
            or command.has_pending_interaction
        ):
            return {"status": "skipped", "items": []}
        agent = getattr(request.app.state, "recommendation_agent", None)
        store = cast(
            RecommendationEventStore | None,
            getattr(request.app.state, "recommendation_event_store", None),
        )
        if agent is None or store is None:
            return {"status": "skipped", "items": []}
        await _publish_diagnostic(request, command, "recommendation_started", {"status": "running"})
        try:
            items = await agent.recommend(
                command.user_question,
                command.assistant_reply,
                command.context_summary,
            )
            if items:
                await store.publish(
                    command.run_id,
                    command.trace_id,
                    command.answer_version,
                    items,
                )
            recommendation_status = "published" if items else "empty"
            await _publish_diagnostic(
                request,
                command,
                "recommendation_completed",
                {"status": recommendation_status, "count": len(items)},
            )
            return {"status": recommendation_status, "items": items}
        except Exception as error:
            _LOGGER.warning(
                "recommendation_sidecar_failed trace_id=%s request_id=%s run_id=%s error_type=%s",
                command.trace_id,
                command.request_id,
                command.run_id,
                type(error).__name__,
            )
            await _publish_diagnostic(
                request, command, "recommendation_completed", {"status": "failed"}
            )
            return {"status": "failed", "items": []}

    @application.post(
        "/internal/v1/conversation-title",
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def generate_conversation_title(
        request: Request,
        command: ConversationTitleCommand,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        """按用户问题与意图结果生成会话标题，并发布标题事件供前端刷新列表。"""
        _validate_run_command(
            request,
            command,
            x_internal_user_id,
            x_internal_user_role,
            x_internal_privacy_status,
        )
        agent = getattr(request.app.state, "title_agent", None)
        if not isinstance(agent, ConversationTitleAgent):
            return {"status": "skipped", "title": None}
        try:
            title = await agent.generate(command.user_question, command.intent_result_json)
        except Exception as error:
            _LOGGER.warning(
                "conversation_title_failed trace_id=%s request_id=%s run_id=%s error_type=%s",
                command.trace_id,
                command.request_id,
                command.run_id,
                type(error).__name__,
            )
            return {"status": "failed", "title": None}
        if not title:
            return {"status": "empty", "title": None}
        store = cast(
            RecommendationEventStore | None,
            getattr(request.app.state, "recommendation_event_store", None),
        )
        if store is not None:
            try:
                await store.publish_conversation_title(
                    command.run_id, command.trace_id, title
                )
            except Exception as error:
                _LOGGER.warning(
                    "conversation_title_event_failed trace_id=%s run_id=%s error_type=%s",
                    command.trace_id,
                    command.run_id,
                    type(error).__name__,
                )
        return {"status": "generated", "title": title}

    @application.post(
        "/internal/v1/memory/retrieve",
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def retrieve_memory(
        request: Request,
        payload: MemoryRetrieveRequest,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        """召回当前登录用户的长期偏好；未配置记忆库时降级为不可用。"""
        user_id = _require_internal_user(
            x_internal_user_id, x_internal_user_role, x_internal_privacy_status
        )
        client = getattr(request.app.state, "memory_client", None)
        if client is None:
            return {"available": False, "memories": "", "message": "长期记忆当前未启用。"}
        return dict(await client.retrieve(user_id, payload.query))

    @application.post(
        "/internal/v1/memory/record",
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def record_memory(
        request: Request,
        payload: MemoryRecordRequest,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        """把当前登录用户的偏好写入长期记忆；未配置记忆库时降级为未保存。"""
        user_id = _require_internal_user(
            x_internal_user_id, x_internal_user_role, x_internal_privacy_status
        )
        client = getattr(request.app.state, "memory_client", None)
        if client is None:
            return {"available": False, "message": "长期记忆当前未启用，本次偏好未被保存。"}
        return dict(await client.record(user_id, payload.content))

    @application.post(
        "/internal/v1/memory/preferences",
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def parse_preferences(
        request: Request,
        payload: PreferenceParseRequest,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        """把记忆原文解析为目录内的结构化偏好；模型失败时返回空结构而不报错。"""
        _require_internal_user(
            x_internal_user_id, x_internal_user_role, x_internal_privacy_status
        )
        agent = getattr(request.app.state, "preference_agent", None)
        if not isinstance(agent, PreferenceParseAgent):
            return {"preferences": {}}
        return {"preferences": await agent.parse(payload.memory_text, payload.catalog)}

    @application.get(
        "/internal/v1/debug/agents",
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def list_debug_agents(
        request: Request,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        """列出可调试直达的智能体及启用状态，不触发任何模型调用。"""
        if (
            not x_internal_user_id
            or x_internal_user_role != "admin"
            or x_internal_privacy_status != "active"
        ):
            raise ServiceError(
                "internal_user_context_mismatch", "内部用户上下文校验失败", False, 403
            )
        master_agent = getattr(request.app.state, "master_agent", None)
        if not isinstance(master_agent, MasterAgent):
            raise ServiceError("master_agent_unavailable", "主控智能体暂不可用", True, 503)
        agents: list[dict[str, object]] = [
            {"name": "masterAgent", "provider_key": None, "enabled": True}
        ]
        agents.extend(
            {
                "name": _provider_agent_name(config.key),
                "provider_key": config.key,
                "enabled": config.enabled,
            }
            for config in master_agent.provider.enabled_configs
        )
        return {"agents": agents}

    @application.post(
        "/internal/v1/debug/agents/{agent_name}",
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def debug_agent(
        agent_name: str,
        request: Request,
        command: DebugAgentCommand,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        """绕过意图识别，把消息直接交给指定智能体，供管理员调试。"""
        if (
            not x_internal_user_id
            or x_internal_user_role != "admin"
            or x_internal_privacy_status != "active"
        ):
            raise ServiceError(
                "internal_user_context_mismatch", "内部用户上下文校验失败", False, 403
            )
        _validate_run_command(
            request,
            command,
            x_internal_user_id,
            x_internal_user_role,
            x_internal_privacy_status,
        )
        master_agent = getattr(request.app.state, "master_agent", None)
        if not isinstance(master_agent, MasterAgent):
            raise ServiceError("master_agent_unavailable", "主控智能体暂不可用", True, 503)
        provider_key = {
            _provider_agent_name(config.key): config.key
            for config in master_agent.provider.enabled_configs
        }.get(agent_name)
        if agent_name != "masterAgent" and provider_key is None:
            raise ServiceError("debug_agent_not_found", "智能体不存在或未启用", False, 404)
        session_id = f"{command.thread_id}:debug:{agent_name}"
        context = AgentContext(
            trace_id=command.trace_id,
            request_id=command.request_id,
            run_id=command.run_id,
            thread_id=command.thread_id,
            conversation_id=command.conversation_id,
            user_id=command.user.user_id,
            role=command.user.role,
            context_summary=command.context_summary,
            diagnostic_callback=lambda event_type, data: _publish_diagnostic(
                request, command, event_type, data
            ),
        )
        if provider_key is None:
            from travel_agent_agent.agents.master.agent import serialize_intent_result_json
            recognition = _debug_unknown_recognition(command.trace_id)
            result = await master_agent.ainvoke(
                command.message,
                command.message,
                serialize_intent_result_json(recognition),
                session_id=session_id,
                context=context,
            )
        else:
            from travel_agent_agent.agents.master.provider import SubAgentRequest

            sub_result = await master_agent.provider.invoke(
                provider_key,
                SubAgentRequest(message=command.message, session_id=session_id, context=context),
            )
            result = {"messages": [{"content": sub_result.content}]}
        return {
            "agent": agent_name,
            "assistant_reply": _extract_assistant_content(result) or "",
            "pending_interaction": _interrupt_payload(result),
        }

    @application.get(
        "/internal/v1/recommendations/{run_id}/events",
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def list_recommendation_events(
        run_id: str,
        request: Request,
        conversation_id: str,
        thread_id: str,
        last_event_id: str | None = None,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        """读取当前 Run 的推荐事件，供 API Server 转发为浏览器 SSE。"""
        if (
            not x_internal_user_id
            or x_internal_user_role not in {"user", "admin"}
            or x_internal_privacy_status != "active"
        ):
            raise ServiceError(
                "internal_user_context_mismatch", "内部用户上下文校验失败", False, 403
            )
        context = get_correlation_context(request)
        if (context.run_id, context.thread_id) != (run_id, thread_id):
            raise ServiceError(
                "internal_correlation_mismatch", "内部调用链标识校验失败", False, 403
            )
        store = cast(
            RecommendationEventStore | None,
            getattr(request.app.state, "recommendation_event_store", None),
        )
        if store is None:
            raise ServiceError(
                "recommendation_event_store_unavailable", "推荐事件服务暂不可用", True, 503
            )
        try:
            events = await store.list_events(run_id, last_event_id)
        except RecommendationEventError as error:
            raise ServiceError(
                "recommendation_event_store_unavailable", "推荐事件服务暂不可用", True, 503
            ) from error
        return {
            "conversation_id": conversation_id,
            "run_id": run_id,
            "thread_id": thread_id,
            "events": events,
        }

    @application.post(
        "/internal/v1/commands/runs/start",
        response_model=RunCommandResult,
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def start_run(
        request: Request,
        command: StartRunCommand,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> RunCommandResult:
        """创建幂等 Redis 检查点并让新 Run 进入 running，暂不调用模型或工具。"""
        _validate_run_command(
            request,
            command,
            x_internal_user_id,
            x_internal_user_role,
            x_internal_privacy_status,
        )
        checkpoint, interrupted = await _execute_run_command(request, command, None)
        return await _with_pending_interaction(
            request, command, checkpoint, include_pending=not interrupted
        )

    @application.post(
        "/internal/v1/commands/runs/resume",
        response_model=RunCommandResult,
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def resume_run(
        request: Request,
        command: ResumeRunCommand,
        x_confirmation_token: Annotated[str | None, Header()] = None,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> RunCommandResult:
        """恢复身份、会话和线程均匹配的非终态 Run，避免跨用户继续执行。"""
        _validate_run_command(
            request,
            command,
            x_internal_user_id,
            x_internal_user_role,
            x_internal_privacy_status,
        )
        checkpoint, interrupted = await _execute_run_command(
            request, command, x_confirmation_token
        )
        return await _with_pending_interaction(
            request, command, checkpoint, include_pending=not interrupted
        )

    @application.post(
        "/internal/v1/commands/runs/cancel",
        response_model=RunCommandResult,
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def cancel_run(
        request: Request,
        command: CancelRunCommand,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> RunCommandResult:
        """将指定 Run 标记为 cancelled；真正的 Agent 执行器接入时需同时监听该状态。"""
        _validate_run_command(
            request,
            command,
            x_internal_user_id,
            x_internal_user_role,
            x_internal_privacy_status,
        )
        checkpoint = await _apply_run_command(request, command)
        return _run_command_result(command, checkpoint)

    @application.post(
        "/internal/v1/commands/runs/{run_id}/interrupt",
        response_model=RunCommandResult,
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def interrupt_run(
        run_id: str,
        request: Request,
        command: CancelRunCommand,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> RunCommandResult:
        """停止指定 Run：本地取消在途执行、清理待交互并广播到其它节点。"""
        if command.run_id != run_id:
            raise ServiceError("internal_run_context_mismatch", "运行上下文校验失败", False, 403)
        _validate_run_command(
            request,
            command,
            x_internal_user_id,
            x_internal_user_role,
            x_internal_privacy_status,
        )
        checkpoint = await _apply_run_command(request, command)
        return _run_command_result(command, checkpoint)

    @application.get(
        "/internal/v1/commands/runs/{run_id}/interaction",
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def get_pending_interaction(
        run_id: str,
        request: Request,
        conversation_id: str,
        thread_id: str,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """读取并轮换当前 Run 的待确认 Token，仅供已认证 API Server 恢复页面。"""
        if x_internal_user_role not in {"user", "admin"} or x_internal_privacy_status != "active":
            raise ServiceError(
                "internal_user_context_mismatch", "内部用户上下文校验失败", False, 403
            )
        if not x_internal_user_id:
            raise ServiceError(
                "internal_user_context_mismatch", "内部用户上下文校验失败", False, 403
            )
        try:
            interaction = await _interaction_store(request).get_for_display(
                x_internal_user_id, conversation_id, run_id, thread_id
            )
        except InteractionError as error:
            raise _interaction_error_to_service_error(error) from error
        return {"pending_interaction": interaction}

    @application.post(
        "/internal/v1/commands/hitl/consume",
        response_model=HITLTokenConsumeResult,
        dependencies=[Depends(require_internal_service)],
        include_in_schema=False,
    )
    async def consume_hitl_token(
        request: Request,
        command: ConsumeHITLTokenCommand,
        x_confirmation_token: Annotated[str | None, Header()] = None,
        x_internal_user_id: Annotated[str | None, Header()] = None,
        x_internal_user_role: Annotated[str | None, Header()] = None,
        x_internal_privacy_status: Annotated[str | None, Header()] = None,
    ) -> HITLTokenConsumeResult:
        """在 API 写事务前原子消费绑定单次工具调用的一次性确认 Token。"""
        _validate_run_command(
            request,
            command,
            x_internal_user_id,
            x_internal_user_role,
            x_internal_privacy_status,
        )
        if not x_confirmation_token:
            raise ServiceError("confirmation_token_required", "缺少确认凭证", False, 409)
        try:
            interaction = await _interaction_store(request).consume(
                command.interaction_id,
                x_confirmation_token,
                user_id=command.user.user_id,
                conversation_id=command.conversation_id,
                run_id=command.run_id,
                thread_id=command.thread_id,
                tool_name=command.tool_name,
                args_hash=canonical_args_hash(command.tool_args),
                decision=command.decision,
            )
        except InteractionError as error:
            raise _interaction_error_to_service_error(error) from error
        return HITLTokenConsumeResult(
            interaction_id=interaction.interaction_id,
            idempotency_key=interaction.interaction_id,
        )


def _read_internal_token(token_file: str) -> str:
    """从只读 Docker Secret 文件加载 Token，缺失、空值或过短时安全拒绝命令。"""
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ServiceError(
            "internal_service_secret_unavailable",
            "内部服务认证暂不可用",
            True,
            503,
        ) from error
    if len(token) < _MINIMUM_TOKEN_LENGTH:
        raise ServiceError(
            "internal_service_secret_invalid",
            "内部服务认证配置无效",
            False,
            503,
        )
    return token


def _interaction_store(request: Request) -> PendingInteractionStore:
    """获取 Redis 待交互仓储；未装配时拒绝确认或页面恢复。"""
    store = getattr(request.app.state, "interaction_store", None)
    if store is None:
        raise ServiceError("interaction_store_unavailable", "确认服务暂不可用", True, 503)
    return cast(PendingInteractionStore, store)


def _checkpoint_store(request: Request) -> RunCheckpointStore:
    """获取 Run 状态索引仓储，集中避免未装配时的属性错误。"""
    return cast(RunCheckpointStore, request.app.state.checkpoint_store)


def _run_registry(request: Request) -> RunExecutionRegistry:
    """获取本节点的在途运行注册表。"""
    registry = getattr(request.app.state, "run_registry", None)
    if not isinstance(registry, RunExecutionRegistry):
        raise ServiceError(
            "run_registry_unavailable", "运行执行器暂不可用", True, 503
        )
    return registry


def _interrupt_coordinator(request: Request) -> RunInterruptCoordinator:
    """获取中断协调器，用于本地取消与跨节点广播。"""
    coordinator = getattr(request.app.state, "interrupt_coordinator", None)
    if not isinstance(coordinator, RunInterruptCoordinator):
        raise ServiceError(
            "interrupt_coordinator_unavailable", "运行中断服务暂不可用", True, 503
        )
    return coordinator


async def _execute_run_command(
    request: Request,
    command: StartRunCommand | ResumeRunCommand,
    confirmation_token: str | None,
) -> tuple[RunCheckpoint, bool]:
    """登记在途运行并执行命令；被中断时返回取消态与中断标记。"""
    coordinator = _interrupt_coordinator(request)
    registry = _run_registry(request)
    await coordinator.preempt(command.conversation_id)
    execution = registry.register(
        command.conversation_id,
        command.run_id,
        user_id=command.user.user_id,
        thread_id=command.thread_id,
    )
    try:
        checkpoint = await _apply_run_command(request, command, confirmation_token)
        return checkpoint, False
    except asyncio.CancelledError:
        _release_current_cancellation()
        await _publish_interrupted(request, command, execution)
        cancelled = await _checkpoint_store(request).cancel(
            command.user.user_id,
            command.conversation_id,
            command.run_id,
            command.thread_id,
        )
        return cancelled, True
    finally:
        registry.finish(command.conversation_id, command.run_id)


def _release_current_cancellation() -> None:
    """递减当前任务的取消计数，使中断后的收尾逻辑仍可继续 await。"""
    task = asyncio.current_task()
    uncancel = getattr(task, "uncancel", None)
    if callable(uncancel):
        uncancel()


async def _publish_interrupted(
    request: Request,
    command: StartRunCommand | ResumeRunCommand,
    execution: RunExecution,
) -> None:
    """发布 interrupted 事件，供 API 与前端标记本轮已被用户停止。"""
    await _publish_diagnostic(
        request,
        command,
        "interrupted",
        {
            "status": "cancelled",
            "reason": "user_interrupted",
            "conversation_id": execution.conversation_id,
        },
    )


def _validate_internal_user_headers(
    user: InternalUserContext,
    user_id: str | None,
    role: str | None,
    privacy_status: str | None,
) -> None:
    """确保受认证 API 的内部身份头与命令正文一致，避免将浏览器字段直接透传给 Agent。"""
    if (user_id, role, privacy_status) != (user.user_id, user.role, user.privacy_status):
        raise ServiceError("internal_user_context_mismatch", "内部用户上下文校验失败", False, 403)


def _validate_run_command(
    request: Request,
    command: _RunCommandBase,
    user_id: str | None,
    role: str | None,
    privacy_status: str | None,
) -> None:
    """校验运行命令的内部用户头、四类关联标识和活动账户状态。"""
    _validate_internal_user_headers(command.user, user_id, role, privacy_status)
    context = get_correlation_context(request)
    if (command.request_id, command.trace_id, command.run_id, command.thread_id) != (
        context.request_id,
        context.trace_id,
        context.run_id,
        context.thread_id,
    ):
        raise ServiceError("internal_correlation_mismatch", "内部调用链标识校验失败", False, 403)
    if command.user.privacy_status != "active":
        raise ServiceError("run_user_not_active", "当前用户状态不允许执行行程任务", False, 409)


async def _apply_run_command(
    request: Request,
    command: StartRunCommand | ResumeRunCommand | CancelRunCommand,
    confirmation_token: str | None = None,
) -> RunCheckpoint:
    """执行检查点状态变更，并将 Redis 故障映射为可安全重试的服务错误。"""
    store = cast(RunCheckpointStore, request.app.state.checkpoint_store)
    try:
        if command.command == "start":
            checkpoint = await store.start(
                command.user.user_id,
                command.conversation_id,
                command.run_id,
                command.thread_id,
            )
            if checkpoint.state.status in _TERMINAL_RUN_STATUSES:
                return checkpoint
            return await _apply_intent_master_route(request, command)
        if command.command == "resume":
            if command.quick_action is not None:
                return await _apply_quick_action(request, command)
            if command.resume_decision is not None:
                return await _resume_pending_interaction(request, command, confirmation_token)
            checkpoint = await store.resume(
                command.user.user_id,
                command.conversation_id,
                command.run_id,
                command.thread_id,
            )
            if checkpoint.state.status in _TERMINAL_RUN_STATUSES:
                return checkpoint
            return await _apply_intent_master_route(request, command)
        checkpoint = await store.cancel(
            command.user.user_id,
            command.conversation_id,
            command.run_id,
            command.thread_id,
        )
        await _interaction_store(request).cancel_run(
            command.user.user_id,
            command.conversation_id,
            command.run_id,
            command.thread_id,
        )
        await _interrupt_coordinator(request).interrupt(
            command.conversation_id, command.run_id
        )
        return checkpoint
    except CheckpointError as error:
        raise _checkpoint_error_to_service_error(error) from error
    except RedisError as error:
        raise ServiceError(
            "checkpoint_store_unavailable", "运行恢复服务暂不可用", True, 503
        ) from error


def _run_command_result(
    command: StartRunCommand | ResumeRunCommand | CancelRunCommand,
    checkpoint: RunCheckpoint,
) -> RunCommandResult:
    """从已校验检查点生成不包含任务正文的内部命令响应。"""
    state = checkpoint.state
    return RunCommandResult(
        command=command.command,
        status=state.status.value,
        conversation_id=command.conversation_id,
        run_id=state.run_id,
        thread_id=state.thread_id,
        version=state.version,
        routing_action=checkpoint.route.action.value if checkpoint.route else None,
        target_agent=checkpoint.route.target_agent if checkpoint.route else None,
        intent_code=checkpoint.route.intent_code if checkpoint.route else None,
        intent_source=checkpoint.route.intent_source if checkpoint.route else None,
        diagnostics=checkpoint.route.diagnostics if checkpoint.route else {},
    )


async def _apply_intent_master_route(
    request: Request,
    command: StartRunCommand | ResumeRunCommand,
) -> RunCheckpoint:
    """对本轮消息执行 L0～L3 并保存 Master 路由结论；上下文只接收 API 脱敏摘要。"""
    intent_agent = cast(IntentRecognitionAgent, request.app.state.intent_agent)
    # 先发布一条"已开始处理"，让前端在识别阶段就有可见进展，而不是空白等待。
    await _publish_diagnostic(
        request,
        command,
        "run_started",
        {"conversation_id": command.conversation_id, "status": "running"},
    )
    try:
        recognition, rewritten_question = await intent_agent.execute_with_rewrite(
            command.task_brief,
            AgentContext(
                trace_id=command.trace_id,
                request_id=command.request_id,
                run_id=command.run_id,
                thread_id=command.thread_id,
                conversation_id=command.conversation_id,
                user_id=command.user.user_id,
                role=command.user.role,
                context_summary=command.context_summary,
            ),
        )
    except Exception as error:
        error_code, retryable = _normalize_execution_error(
            error, "intent_recognition_failed"
        )
        _log_execution_failure(command, error, error_code)
        await _publish_diagnostic(
            request,
            command,
            "run_error",
            {
                "stage": "intent_recognition",
                "error_type": "provider_error",
                "error_code": error_code,
                "retryable": retryable,
            },
        )
        route = RunRoute(
            RouteAction.CLARIFY,
            "masterAgent",
            None,
            None,
            {
                "intent_execution": "failed",
                "intent_error_code": error_code,
                "intent_retryable": retryable,
            },
        )
        return await _checkpoint_store(request).apply_route(
            command.user.user_id,
            command.conversation_id,
            command.run_id,
            command.thread_id,
            route,
            RunStatus.FAILED,
        )
    await _publish_diagnostic(
        request,
        command,
        "intent_recognition",
        {
            "source": recognition.source.value,
            "intent_code": recognition.primary_intent,
            "confidence": recognition.intents[0].confidence.value,
            "reason": recognition.intents[0].reason,
            "multi_intent": recognition.multi_intent,
            "diagnostics": recognition.diagnostics,
        },
    )
    rewrite_called = bool(recognition.diagnostics.get("rewrite_called"))
    rewrite_data: dict[str, object] = {
        "status": "completed" if rewrite_called else "skipped",
        "reason": "executed_after_l1_l2_miss" if rewrite_called else "l2_hit_or_l1_hit",
        "changed": rewritten_question.strip() != command.task_brief.strip(),
    }
    if rewrite_called:
        rewrite_data["rewritten_question"] = SensitiveMasker().mask_text(rewritten_question)[:8000]
    await _publish_diagnostic(request, command, "query_rewrite", rewrite_data)
    route, target_status = route_from_recognition(recognition)
    route.diagnostics["master_input"] = {
        "rewritten_question_present": bool(rewritten_question),
        "intent_result_json_present": True,
    }
    await _publish_diagnostic(
        request,
        command,
        "route_selected",
        {
            "action": route.action.value,
            "target_agent": route.target_agent,
            "intent_code": route.intent_code,
            "intent_source": route.intent_source,
        },
    )
    async def publish_knowledge_diagnostic(event_type: str, data: dict[str, object]) -> None:
        """将 InfoAgent 的安全知识库诊断事件写入当前 Run 事件流。"""
        await _publish_diagnostic(request, command, event_type, data)

    master_agent = getattr(request.app.state, "master_agent", None)
    if isinstance(master_agent, MasterAgent):
        await _publish_diagnostic(request, command, "master_started", {"status": "running"})
        if route.action is RouteAction.DIRECT_DISPATCH and route.target_agent != "masterAgent":
            await _publish_diagnostic(
                request,
                command,
                "sub_agent_started",
                {"agent": route.target_agent, "status": "running"},
            )
        usage_token, usage = bind_token_usage()
        try:
            try:
                result = await _stream_master_reply(
                    request,
                    command,
                    master_agent,
                    rewritten_question,
                    recognition.model_dump_json(),
                    publish_knowledge_diagnostic,
                )
            finally:
                reset_token_usage(usage_token)
            # 逐轮累计的真实用量只以统计口径进入诊断，不包含任何消息正文。
            route.diagnostics["token_usage"] = usage.as_dict()
            await _publish_diagnostic(
                request, command, "token_usage", dict(usage.as_dict())
            )
            route.diagnostics["master_execution"] = "invoked"
            interrupt_kind = await _issue_graph_interrupt(
                request, command, result, command.thread_id
            )
            if interrupt_kind == "clarification":
                target_status = RunStatus.CLARIFYING
            elif interrupt_kind == "approval":
                target_status = RunStatus.AWAITING_APPROVAL
            else:
                # 无可恢复中断且已产出回复，说明本轮已正常完成。
                target_status = RunStatus.COMPLETED
            if route.action is RouteAction.DIRECT_DISPATCH and route.target_agent != "masterAgent":
                await _publish_diagnostic(
                    request,
                    command,
                    "sub_agent_completed",
                    {"agent": route.target_agent, "status": "completed"},
                )
            await _publish_diagnostic(
                request, command, "master_completed", {"status": "completed"}
            )
            await _publish_assistant_reply(request, command, result)
        except Exception as error:
            # 仅记录稳定错误类型/错误码，不记录用户消息、密钥或异常正文。
            error_code, retryable = _normalize_execution_error(
                error, "master_agent_execution_failed"
            )
            route.diagnostics.update(
                {
                    "master_execution": "failed",
                    "master_error_type": "provider_error",
                    "master_error_code": error_code,
                    "master_retryable": retryable,
                }
            )
            _LOGGER.error(
                "master_agent_execution_failed trace_id=%s request_id=%s "
                "run_id=%s thread_id=%s error_type=%s error_code=%s retryable=%s chain=%s",
                command.trace_id,
                command.request_id,
                command.run_id,
                command.thread_id,
                type(error).__name__,
                error_code,
                retryable,
                _describe_exception_chain(error),
            )
            await _publish_diagnostic(
                request,
                command,
                "run_error",
                {
                    "stage": "master",
                    "error_type": "provider_error",
                    "error_code": error_code,
                    "retryable": retryable,
                },
            )
            if route.action is RouteAction.DIRECT_DISPATCH and route.target_agent != "masterAgent":
                await _publish_diagnostic(
                    request,
                    command,
                    "sub_agent_completed",
                    {"agent": route.target_agent, "status": "failed"},
                )
            target_status = RunStatus.FAILED
    store = cast(RunCheckpointStore, request.app.state.checkpoint_store)
    return await store.apply_route(
        command.user.user_id,
        command.conversation_id,
        command.run_id,
        command.thread_id,
        route,
        target_status,
    )


async def _resume_pending_interaction(
    request: Request,
    command: ResumeRunCommand,
    confirmation_token: str | None,
) -> RunCheckpoint:
    """验证待交互决定后使用同一 LangGraph 主线程恢复，编辑则重新规划。"""
    decision = command.resume_decision
    if decision is None:
        raise ServiceError("resume_decision_required", "缺少恢复决定", False, 422)
    interactions = _interaction_store(request)
    try:
        interaction = await interactions.validate_decision(
            decision.interaction_id,
            confirmation_token,
            user_id=command.user.user_id,
            conversation_id=command.conversation_id,
            run_id=command.run_id,
            thread_id=command.thread_id,
            decision=decision.decision,
        )
    except InteractionError as error:
        raise _interaction_error_to_service_error(error) from error
    if interaction.kind == "approval" and decision.decision == "approve":
        # 用户已提交批准决定：恢复执行需要模型多轮推理才会真正调用写工具并消费
        # Token，这段窗口内若继续向查询接口暴露同一交互，前端会反复弹出确认卡片
        # 并与已到达的助手回复冲突。这里只摘除展示索引，Token 仍保留以便原子消费。
        await interactions.hide_from_display(
            interaction.interaction_id,
            user_id=command.user.user_id,
            conversation_id=command.conversation_id,
            run_id=command.run_id,
            thread_id=command.thread_id,
        )
    if decision.decision == "edit":
        await interactions.reject(
            interaction.interaction_id,
            user_id=command.user.user_id,
            conversation_id=command.conversation_id,
            run_id=command.run_id,
            thread_id=command.thread_id,
        )
        return await _apply_intent_master_route(request, command)
    checkpoint = await _checkpoint_store(request).resume(
        command.user.user_id,
        command.conversation_id,
        command.run_id,
        command.thread_id,
    )
    if checkpoint.state.status in _TERMINAL_RUN_STATUSES:
        return checkpoint
    master_agent = getattr(request.app.state, "master_agent", None)
    if not isinstance(master_agent, MasterAgent):
        raise ServiceError("master_agent_unavailable", "主控智能体暂不可用", True, 503)
    if decision.decision == "reject":
        await interactions.reject(
            interaction.interaction_id,
            user_id=command.user.user_id,
            conversation_id=command.conversation_id,
            run_id=command.run_id,
            thread_id=command.thread_id,
        )
    if decision.decision == "respond":
        # 澄清类交互用完即关闭，避免恢复成功后前端仍显示旧问题卡片。
        await interactions.consume_answered(
            interaction.interaction_id,
            user_id=command.user.user_id,
            conversation_id=command.conversation_id,
            run_id=command.run_id,
            thread_id=command.thread_id,
        )
    resume_value: dict[str, Any]
    if interaction.kind == "clarification":
        resume_value = {"message": decision.message or command.task_brief}
    else:
        resume_value = {
            "decisions": [
                {
                    "type": "approve" if decision.decision == "approve" else "reject",
                    **({"message": decision.message} if decision.message else {}),
                }
            ]
        }
    context = AgentContext(
        trace_id=command.trace_id,
        request_id=command.request_id,
        run_id=command.run_id,
        thread_id=command.thread_id,
        conversation_id=command.conversation_id,
        user_id=command.user.user_id,
        role=command.user.role,
        context_summary=command.context_summary,
        diagnostic_callback=lambda event_type, data: _publish_diagnostic(
            request, command, event_type, data
        ),
    )
    authorization = None
    if interaction.kind == "approval" and decision.decision == "approve":
        authorization = bind_tool_execution_authorization(
            ToolExecutionAuthorization(interaction.interaction_id, confirmation_token or "")
        )
    try:
        result = await master_agent.aresume(resume_value, command.thread_id, context)
        await _publish_assistant_reply(request, command, result)
    finally:
        if authorization is not None:
            reset_tool_execution_authorization(authorization)
    target = RunStatus.RUNNING
    interrupt_kind = await _issue_graph_interrupt(request, command, result, command.thread_id)
    if interrupt_kind == "clarification":
        target = RunStatus.CLARIFYING
    elif interrupt_kind == "approval":
        target = RunStatus.AWAITING_APPROVAL
    else:
        target = RunStatus.COMPLETED
    route = checkpoint.route or RunRoute(RouteAction.CLARIFY, "masterAgent", None, None)
    return await _checkpoint_store(request).apply_route(
        command.user.user_id,
        command.conversation_id,
        command.run_id,
        command.thread_id,
        route,
        target,
    )


async def _apply_quick_action(
    request: Request, command: ResumeRunCommand
) -> RunCheckpoint:
    """校验快速操作并沿用既有直达路由恢复当前子 Agent。"""
    quick_action = command.quick_action
    if quick_action is None or not continuation_signals.is_valid(quick_action):
        raise ServiceError("quick_action_invalid", "快速操作无效", False, 422)
    checkpoint = await _checkpoint_store(request).resume(
        command.user.user_id,
        command.conversation_id,
        command.run_id,
        command.thread_id,
    )
    if checkpoint.state.status in _TERMINAL_RUN_STATUSES:
        raise ServiceError("quick_action_route_unavailable", "当前运行无法快速续跑", False, 409)
    route = checkpoint.route
    if route is None or route.action is not RouteAction.DIRECT_DISPATCH:
        raise ServiceError("quick_action_route_unavailable", "当前运行无法快速续跑", False, 409)
    master_agent = getattr(request.app.state, "master_agent", None)
    if not isinstance(master_agent, MasterAgent):
        raise ServiceError("master_agent_unavailable", "主控智能体暂不可用", True, 503)
    from travel_agent_agent.agents.master.provider import SubAgentRequest

    context = AgentContext(
        trace_id=command.trace_id,
        request_id=command.request_id,
        run_id=command.run_id,
        thread_id=command.thread_id,
        conversation_id=command.conversation_id,
        user_id=command.user.user_id,
        role=command.user.role,
        context_summary=command.context_summary,
        diagnostic_callback=lambda event_type, data: _publish_diagnostic(
            request, command, event_type, data
        ),
    )
    provider_key = _ROUTE_PROVIDER_KEYS.get(route.target_agent)
    if provider_key is None:
        raise ServiceError("quick_action_route_unavailable", "当前运行无法快速续跑", False, 409)
    result = await master_agent.provider.invoke(
        provider_key,
        SubAgentRequest(
            message=continuation_signals.normalize(quick_action),
            session_id=f"{command.thread_id}:{provider_key}",
            context=context,
        ),
    )
    await _publish_assistant_reply(request, command, result)
    target_status = RunStatus.RUNNING
    if result.pending_interaction is not None:
        interrupt_kind = await _issue_graph_interrupt(
            request,
            command,
            {"__interrupt__": [result.pending_interaction]},
            command.thread_id,
        )
        if interrupt_kind == "clarification":
            target_status = RunStatus.CLARIFYING
        elif interrupt_kind == "approval":
            target_status = RunStatus.AWAITING_APPROVAL
        else:
            target_status = RunStatus.COMPLETED
    else:
        target_status = RunStatus.COMPLETED
    return await _checkpoint_store(request).apply_route(
        command.user.user_id,
        command.conversation_id,
        command.run_id,
        command.thread_id,
        route,
        target_status,
    )


async def _with_pending_interaction(
    request: Request,
    command: StartRunCommand | ResumeRunCommand | CancelRunCommand,
    checkpoint: RunCheckpoint,
    *,
    include_pending: bool = True,
) -> RunCommandResult:
    """将当前 Run 的待交互安全摘要附加到内部命令响应。"""
    result = _run_command_result(command, checkpoint)
    assistant_reply = getattr(request.state, "assistant_reply", None)
    if isinstance(assistant_reply, str):
        result = result.model_copy(update={"assistant_reply": assistant_reply})
    if command.command == "cancel" or not include_pending:
        return result
    try:
        pending = await _interaction_store(request).get_for_display(
            command.user.user_id,
            command.conversation_id,
            command.run_id,
            command.thread_id,
        )
    except InteractionError as error:
        raise _interaction_error_to_service_error(error) from error
    return result.model_copy(update={"pending_interaction": pending})


async def _publish_assistant_reply(
    request: Request,
    command: StartRunCommand | ResumeRunCommand,
    result: object,
) -> None:
    """提取并脱敏主回复，发布旁路事件且不让事件故障影响主 Run。"""
    content = _extract_assistant_content(result)
    if not content:
        return
    sanitized = SensitiveMasker().mask_text(content)[:16000]
    request.state.assistant_reply = sanitized
    store = cast(
        RecommendationEventStore | None,
        getattr(request.app.state, "recommendation_event_store", None),
    )
    if store is None:
        return
    try:
        await store.publish_assistant_reply(
            command.run_id,
            command.trace_id,
            f"{command.run_id}-{hashlib.sha256(sanitized.encode('utf-8')).hexdigest()[:16]}",
            sanitized,
        )
    except Exception as error:
        _LOGGER.warning(
            "assistant_event_publish_failed trace_id=%s request_id=%s run_id=%s error_type=%s",
            command.trace_id,
            command.request_id,
            command.run_id,
            type(error).__name__,
        )


async def _publish_diagnostic(
    request: Request,
    command: StartRunCommand | ResumeRunCommand | RecommendationCommand | DebugAgentCommand,
    event_type: str,
    data: dict[str, object],
) -> None:
    """发布安全执行阶段事件；事件存储故障只记录 warning，不影响主流程。"""
    store = cast(
        RecommendationEventStore | None,
        getattr(request.app.state, "recommendation_event_store", None),
    )
    if store is None:
        return
    try:
        await store.publish_diagnostic(command.run_id, command.trace_id, event_type, data)
    except Exception as error:
        _LOGGER.warning(
            "diagnostic_event_publish_failed trace_id=%s request_id=%s run_id=%s "
            "event_type=%s error_type=%s",
            command.trace_id,
            command.request_id,
            command.run_id,
            event_type,
            type(error).__name__,
        )


def _require_internal_user(
    user_id: str | None, role: str | None, privacy_status: str | None
) -> str:
    """校验内部调用携带的用户身份，返回可用的 user_id。"""
    if not user_id or role not in {"user", "admin"} or privacy_status != "active":
        raise ServiceError(
            "internal_user_context_mismatch", "内部用户上下文校验失败", False, 403
        )
    return user_id


async def _stream_master_reply(
    request: Request,
    command: StartRunCommand | ResumeRunCommand,
    master_agent: MasterAgent,
    rewritten_question: str,
    intent_result_json: str,
    diagnostic_callback: Callable[[str, dict[str, object]], Awaitable[None]],
) -> object:
    """流式执行主控并把回答分片发布为 token 事件，最后返回完整图状态。"""
    context = AgentContext(
        trace_id=command.trace_id,
        request_id=command.request_id,
        run_id=command.run_id,
        thread_id=command.thread_id,
        conversation_id=command.conversation_id,
        user_id=command.user.user_id,
        role=command.user.role,
        context_summary=command.context_summary,
        diagnostic_callback=diagnostic_callback,
    )
    buffer = ""
    result: object = None
    async for kind, payload in master_agent.astream(
        command.task_brief,
        rewritten_question,
        intent_result_json,
        session_id=command.thread_id,
        context=context,
    ):
        if kind != "delta":
            result = payload
            continue
        buffer += str(payload)
        # 合并小分片后再发布，避免每个 token 都写一次事件存储。
        if len(buffer) >= _TOKEN_FLUSH_CHARS:
            await _publish_diagnostic(request, command, "token", {"text": buffer})
            buffer = ""
    if buffer:
        await _publish_diagnostic(request, command, "token", {"text": buffer})
    return result


def _provider_agent_name(provider_key: str) -> str:
    """把子 Agent 的 provider key 还原为对外的 camelCase 名称。"""
    return _PROVIDER_AGENT_NAMES.get(provider_key, provider_key)


def _debug_unknown_recognition(trace_id: str) -> IntentRecognitionResult:
    """调试主控时构造固定的 unknown 意图，避免伪造识别结果。"""
    return IntentRecognitionResult(
        intents=(
            RecognizedIntent(
                intent="unknown",
                target_agent="masterAgent",
                confidence=IntentConfidence.LOW,
                reason="管理员调试直达主控。",
            ),
        ),
        primary_intent="unknown",
        multi_intent=False,
        overall_reason="管理员调试直达主控。",
        source=IntentSource.LLM,
        trace_id=trace_id,
    )


def _extract_assistant_content(result: object) -> str | None:
    """从 LangGraph 或子 Agent 结果读取最后一条面向用户的文本。"""
    if isinstance(result, dict):
        messages = result.get("messages")
        if isinstance(messages, list) and messages:
            content = getattr(messages[-1], "content", None)
            if content is None and isinstance(messages[-1], dict):
                content = messages[-1].get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    content = getattr(result, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    return None


async def _issue_graph_interrupt(
    request: Request,
    command: StartRunCommand | ResumeRunCommand,
    result: object,
    graph_thread_id: str,
) -> str | None:
    """从 LangGraph 输出提取显式澄清中断并保存其恢复索引。"""
    payload = _interrupt_payload(result)
    if payload is None:
        return None
    kind = payload.get("kind")
    if kind == "approval":
        action = payload.get("action")
        if not isinstance(action, dict):
            return None
        tool_name = action.get("tool_name")
        tool_args = action.get("tool_args")
        if not isinstance(tool_name, str) or not isinstance(tool_args, dict):
            return None
        interaction = PendingInteraction(
            interaction_id=f"hitl_{uuid.uuid4().hex}",
            kind="approval",
            status="issued",
            user_id=command.user.user_id,
            conversation_id=command.conversation_id,
            run_id=command.run_id,
            thread_id=command.thread_id,
            graph_thread_id=graph_thread_id,
            tool_name=tool_name,
            args_hash=canonical_args_hash(tool_args),
            allowed_decisions=("approve", "reject", "edit"),
            summary={
                "tool_name": tool_name,
                "args_hash": canonical_args_hash(tool_args),
                "message": "该操作会修改差旅业务数据，请确认后继续。",
            },
            token_hash=None,
            previous_token_hash=None,
            action_version=1,
            created_at="",
        )
        await _interaction_store(request).issue(interaction)
        return "approval"
    if kind != "clarification":
        return None
    interaction = PendingInteraction(
        interaction_id=f"hitl_{uuid.uuid4().hex}",
        kind="clarification",
        status="issued",
        user_id=command.user.user_id,
        conversation_id=command.conversation_id,
        run_id=command.run_id,
        thread_id=command.thread_id,
        graph_thread_id=graph_thread_id,
        tool_name=None,
        args_hash=None,
        allowed_decisions=("respond",),
        summary={
            "ui_type": payload.get("ui_type", "text"),
            "question": payload.get("question", "请补充必要信息。"),
            "options": payload.get("options", []),
            "fields": payload.get("fields", []),
        },
        token_hash=None,
        previous_token_hash=None,
        action_version=1,
        created_at="",
    )
    await _interaction_store(request).issue(interaction)
    return "clarification"


def _interrupt_payload(result: object) -> dict[str, Any] | None:
    """兼容 LangGraph Interrupt 对象与字典格式，读取第一个中断值。"""
    if not isinstance(result, dict):
        return None
    interrupts = result.get("__interrupt__")
    if not isinstance(interrupts, (list, tuple)) or not interrupts:
        return None
    value = getattr(interrupts[0], "value", interrupts[0])
    if isinstance(value, dict):
        if value.get("kind"):
            return value
        action_requests = value.get("action_requests")
        if isinstance(action_requests, list) and action_requests:
            action = action_requests[0]
            if isinstance(action, dict):
                return {
                    "kind": "approval",
                    "action": {
                        "tool_name": action.get("name"),
                        "tool_args": action.get("args"),
                    },
                }
            return {
                "kind": "approval",
                "action": {
                    "tool_name": getattr(action, "name", None),
                    "tool_args": getattr(action, "args", None),
                },
            }
    actions = getattr(value, "action_requests", None)
    if actions:
        action = actions[0]
        return {
            "kind": "approval",
            "action": {
                "tool_name": getattr(action, "name", None),
                "tool_args": getattr(action, "args", None),
            },
        }
    return None


def _normalize_execution_error(error: Exception, default_code: str) -> tuple[str, bool]:
    """从异常链提取稳定 Provider 错误码，避免把 SDK 类型或响应正文暴露给前端。"""
    stable_codes = {
        "dashscope_quota_exhausted",
        "dashscope_auth_failed",
        "dashscope_model_unavailable",
        "dashscope_upstream_unavailable",
    }
    upstream_codes = {
        "insufficient_quota": ("dashscope_quota_exhausted", False),
        "authentication_error": ("dashscope_auth_failed", False),
        "invalid_api_key": ("dashscope_auth_failed", False),
        "model_not_found": ("dashscope_model_unavailable", False),
        "model_access_denied": ("dashscope_model_unavailable", False),
        "permission_denied": ("dashscope_model_unavailable", False),
    }
    current: BaseException | None = error
    visited: set[int] = set()
    for _ in range(8):
        if current is None or id(current) in visited:
            break
        visited.add(id(current))
        retryable = bool(getattr(current, "retryable", False))
        candidates: list[object] = [getattr(current, "code", None)]
        body = getattr(current, "body", None)
        if isinstance(body, dict):
            nested = body.get("error")
            if isinstance(nested, dict):
                candidates.extend((nested.get("code"), nested.get("type")))
            candidates.extend((body.get("code"), body.get("type")))
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            normalized = candidate.strip().lower()
            if normalized in stable_codes:
                return normalized, retryable or normalized == "dashscope_upstream_unavailable"
            if normalized in upstream_codes:
                return upstream_codes[normalized]
        status_code = getattr(current, "status_code", None)
        if status_code in {500, 502, 503, 504}:
            return "dashscope_upstream_unavailable", True
        current = current.__cause__ or current.__context__
    return default_code, False


def _checkpoint_error_to_service_error(error: CheckpointError) -> ServiceError:
    """将检查点的有限失败原因转换成 API 可识别而不泄漏基础设施细节的错误。"""
    if str(error) == "checkpoint_not_found_or_expired":
        return ServiceError("run_checkpoint_unavailable", "运行已过期或无法恢复", False, 409)
    if str(error) == "checkpoint_identity_mismatch":
        return ServiceError("internal_run_context_mismatch", "运行上下文校验失败", False, 403)
    if str(error) == "checkpoint_concurrent_update":
        return ServiceError("checkpoint_update_conflict", "运行状态正在更新，请重试", True, 409)
    return ServiceError("run_checkpoint_invalid", "运行检查点不可用", False, 409)


def _interaction_error_to_service_error(error: InteractionError) -> ServiceError:
    """将待确认 Token 的受控失败映射为 API Server 可处理的安全错误。"""
    code = str(error)
    if code == "interaction_not_found_or_expired":
        return ServiceError("confirmation_expired", "确认已过期，请重新发起操作", False, 409)
    if code in {"interaction_identity_mismatch", "interaction_action_mismatch"}:
        return ServiceError("confirmation_context_mismatch", "确认上下文校验失败", False, 403)
    if code in {"confirmation_token_invalid", "interaction_not_consumable"}:
        return ServiceError("confirmation_token_invalid", "确认凭证无效或已使用", False, 409)
    if code == "interaction_concurrent_update":
        return ServiceError("confirmation_update_conflict", "确认状态正在更新，请重试", True, 409)
    return ServiceError("confirmation_rejected", "确认请求未被接受", False, 409)


def _describe_exception_chain(error: BaseException, limit: int = 6) -> str:
    """拼接异常链的类型与截断消息，供排障使用；不包含用户正文或密钥。"""
    parts: list[str] = []
    current: BaseException | None = error
    visited: set[int] = set()
    for _ in range(limit):
        if current is None or id(current) in visited:
            break
        visited.add(id(current))
        message = str(current).replace("\n", " ")[:120]
        parts.append(f"{type(current).__name__}: {message}")
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)[:400]


def _log_execution_failure(
    command: StartRunCommand | ResumeRunCommand, error: Exception, error_code: str
) -> None:
    """记录意图识别失败的错误码与异常链，不记录用户消息正文。"""
    _LOGGER.error(
        "intent_recognition_failed trace_id=%s request_id=%s run_id=%s "
        "error_type=%s error_code=%s chain=%s",
        command.trace_id,
        command.request_id,
        command.run_id,
        type(error).__name__,
        error_code,
        _describe_exception_chain(error),
    )
