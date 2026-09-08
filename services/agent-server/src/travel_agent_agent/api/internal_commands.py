# 本文件定义 API Server 调用 Agent Server 的受保护内部命令边界。
# 定义内部用户上下文、停止记忆命令和结果。
# 定义 Docker Secret 认证、路由注册和二次身份校验函数。
from __future__ import annotations

import secrets
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError

from travel_agent_agent.agents.base import AgentContext
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
from travel_agent_agent.orchestration.checkpoints import (
    CheckpointError,
    RunCheckpoint,
    RunCheckpointStore,
)
from travel_agent_agent.orchestration.interactions import (
    InteractionError,
    PendingInteraction,
    PendingInteractionStore,
    canonical_args_hash,
)
from travel_agent_agent.orchestration.routing import RouteAction, RunRoute, route_from_recognition
from travel_agent_agent.orchestration.status import RunStatus

_USER_ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"
_MINIMUM_TOKEN_LENGTH = 32
_SUSPENDABLE_PRIVACY_STATES = frozenset({"deletion_pending", "deleted"})
_TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED}
)


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
        checkpoint = await _apply_run_command(request, command)
        return await _with_pending_interaction(request, command, checkpoint)

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
        checkpoint = await _apply_run_command(request, command, x_confirmation_token)
        return await _with_pending_interaction(request, command, checkpoint)

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
    route, target_status = route_from_recognition(recognition)
    route.diagnostics["master_input"] = {
        "rewritten_question_present": bool(rewritten_question),
        "intent_result_json_present": True,
    }
    master_agent = getattr(request.app.state, "master_agent", None)
    if isinstance(master_agent, MasterAgent):
        try:
            result = await master_agent.ainvoke(
                command.task_brief,
                rewritten_question,
                recognition.model_dump_json(),
                session_id=command.thread_id,
                context=AgentContext(
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
            route.diagnostics["master_execution"] = "invoked"
            interrupt_kind = await _issue_graph_interrupt(
                request, command, result, command.thread_id
            )
            if interrupt_kind == "clarification":
                target_status = RunStatus.CLARIFYING
            elif interrupt_kind == "approval":
                target_status = RunStatus.AWAITING_APPROVAL
        except Exception:
            route.diagnostics["master_execution"] = "unavailable"
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
    )
    authorization = None
    if interaction.kind == "approval" and decision.decision == "approve":
        authorization = bind_tool_execution_authorization(
            ToolExecutionAuthorization(interaction.interaction_id, confirmation_token or "")
        )
    try:
        result = await master_agent.aresume(resume_value, command.thread_id, context)
    finally:
        if authorization is not None:
            reset_tool_execution_authorization(authorization)
    target = RunStatus.RUNNING
    interrupt_kind = await _issue_graph_interrupt(request, command, result, command.thread_id)
    if interrupt_kind == "clarification":
        target = RunStatus.CLARIFYING
    elif interrupt_kind == "approval":
        target = RunStatus.AWAITING_APPROVAL
    route = checkpoint.route or RunRoute(RouteAction.CLARIFY, "masterAgent", None, None)
    return await _checkpoint_store(request).apply_route(
        command.user.user_id,
        command.conversation_id,
        command.run_id,
        command.thread_id,
        route,
        target,
    )


async def _with_pending_interaction(
    request: Request,
    command: StartRunCommand | ResumeRunCommand | CancelRunCommand,
    checkpoint: RunCheckpoint,
) -> RunCommandResult:
    """将当前 Run 的待交互安全摘要附加到内部命令响应。"""
    result = _run_command_result(command, checkpoint)
    if command.command == "cancel":
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
