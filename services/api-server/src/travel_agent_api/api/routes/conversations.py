# 本文件提供持久化会话和 Run 的最小 REST 路由。
# 定义 list_conversations、start_run 与 get_run，分别查询会话、原子创建初始 Run 和查询其状态。
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any, Literal, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field, model_validator

from travel_agent_api.api.dependencies import get_current_user, record_audit_event, require_csrf
from travel_agent_api.api.errors import ServiceError
from travel_agent_api.application.auth_service import AuthenticatedUser
from travel_agent_api.core.correlation import CorrelationContext, get_correlation_context
from travel_agent_api.infrastructure.agent_client import (
    AgentClient,
    AgentCommandError,
    InternalUserContext,
)
from travel_agent_api.persistence.services import PostgresConversationService, RunSummary

router = APIRouter(prefix="/api/v1", tags=["conversations"])
logger = logging.getLogger("travel_agent_api.conversations")
# SSE 事件轮询：活跃期用很短间隔保证步骤逐条出现，空闲期放慢以降低内部请求量。
_EVENT_POLL_INTERVAL_SECONDS = 0.25
_EVENT_POLL_IDLE_INTERVAL_SECONDS = 1.0
_EVENT_POLL_ACTIVE_SECONDS = 45.0
# 单条 SSE 连接的总时长上限，超时后由浏览器携带 Last-Event-ID 重连续传。
_EVENT_STREAM_WINDOW_SECONDS = 600.0
# 收到这些事件说明本轮已给出结论，可以结束当前连接。
_EVENT_STREAM_END_TYPES = frozenset({"assistant_message", "interrupted"})


class TaskRequest(BaseModel):
    """定义本轮用户消息；带 conversation_id 时在同一会话内开启新一轮。"""

    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=64)


class RenameConversationRequest(BaseModel):
    """校验会话改名请求，只接受受限长度的标题文本。"""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=64)


class MessageFeedbackRequest(BaseModel):
    """校验消息反馈请求；null 表示清除已有反馈。"""

    model_config = ConfigDict(extra="forbid")
    feedback: Literal["up", "down"] | None = None


class ResumeRequest(BaseModel):
    """校验普通补充消息或结构化 HITL 恢复决定，禁止将 Token 写入消息表。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["message", "decision", "quick_action"] = "message"
    message: str | None = Field(default=None, max_length=8000)
    interaction_id: str | None = Field(default=None, min_length=1, max_length=128)
    decision: Literal["approve", "reject", "edit", "respond"] | None = None
    confirmation_token: str | None = Field(default=None, min_length=20, max_length=512)
    edited_args: dict[str, object] | None = None
    quick_action: str | None = Field(default=None, min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_kind(self) -> ResumeRequest:
        """确保普通消息与 HITL 恢复决定的必填字段互不混淆。"""
        if self.kind == "message" and (
            not self.message or not self.message.strip() or self.quick_action or self.decision
        ):
            raise ValueError("resume_message_required")
        if self.kind == "decision":
            if (
                not self.interaction_id
                or self.decision is None
                or self.quick_action is not None
            ):
                raise ValueError("resume_decision_required")
            if self.decision in {"approve", "reject", "edit"} and not self.confirmation_token:
                raise ValueError("confirmation_token_required")
            if self.decision == "respond" and not (self.message and self.message.strip()):
                raise ValueError("resume_message_required")
            if self.decision == "edit" and not self.edited_args and not (
                self.message and self.message.strip()
            ):
                raise ValueError("resume_message_required")
        if self.kind == "quick_action" and (
            not self.quick_action
            or self.message
            or self.decision
            or self.confirmation_token
            or self.edited_args
        ):
            raise ValueError("quick_action_required")
        return self



def _conversation_service(request: Request) -> PostgresConversationService:
    """读取已启用的持久化服务；未启用时明确拒绝而不是退回内存。"""
    service = getattr(request.app.state, "conversation_service", None)
    if service is None:
        raise ServiceError("persistence_not_enabled", "持久化服务尚未启用", False, 503)
    return cast(PostgresConversationService, service)


def _agent_client(request: Request) -> AgentClient:
    """获取仅供 API Server 使用的 Agent 内部命令客户端。"""
    return cast(AgentClient, request.app.state.agent_client)


def _internal_user(user: AuthenticatedUser) -> InternalUserContext:
    """从已认证会话构造固定活动状态的最小内部用户上下文。"""
    return InternalUserContext(user.user_id, user.role, "active")


def _intent_json(agent_run: Any) -> str:
    """构造标题生成所需的意图 JSON，只包含意图码与识别来源。"""
    return json.dumps(
        {
            "primary_intent": getattr(agent_run, "intent_code", None) or "",
            "intent_source": getattr(agent_run, "intent_source", None) or "",
        },
        ensure_ascii=False,
    )


def _set_run_correlation(
    request: Request, run_id: str, thread_id: str
) -> CorrelationContext:
    """将已落库的 Run 与线程标识写回当前调用链，供 Agent、日志和审计统一使用。"""
    context = replace(get_correlation_context(request), run_id=run_id, thread_id=thread_id)
    request.state.correlation_context = context
    return context


def _agent_command_error(error: AgentCommandError) -> ServiceError:
    """将 Agent 内部命令失败收敛为浏览器可安全处理的 API 错误。"""
    status_code = 503 if error.retryable else 409
    return ServiceError(error.code, "智能体运行服务暂不可用", error.retryable, status_code)


@router.get("/conversations")
async def list_conversations(
    request: Request, user: AuthenticatedUser = Depends(get_current_user)
) -> dict[str, object]:
    """返回当前用户自己的未删除会话摘要。"""
    conversations = await _conversation_service(request).list_conversations(user.user_id)
    return {
        "conversations": [
            {
                "conversation_id": item.conversation_id,
                "title": item.title,
                "updated_at": item.updated_at.isoformat(),
            }
            for item in conversations
        ]
    }


@router.post("/conversations/runs", status_code=202, dependencies=[Depends(require_csrf)])
async def start_run(
    payload: TaskRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    """创建 queued Run：无 conversation_id 时新建会话，否则在同一会话内开启新一轮。"""
    conversation_service = _conversation_service(request)
    context_summary = ""
    if payload.conversation_id is None:
        run = await conversation_service.start_run(
            user.user_id, payload.message, get_correlation_context(request)
        )
    else:
        context_summary = await conversation_service.get_desensitized_context_summary(
            user.user_id, payload.conversation_id
        )
        turn = await conversation_service.start_turn(
            user.user_id,
            payload.conversation_id,
            payload.message,
            get_correlation_context(request),
        )
        if turn is None:
            raise ServiceError("conversation_not_found", "会话不存在或无权访问", False, 404)
        run = turn
    correlation = _set_run_correlation(request, run.run_id, run.thread_id)
    background_tasks.add_task(
        _dispatch_start_run,
        _agent_client(request),
        conversation_service,
        request.app.state.audit_service,
        _internal_user(user),
        correlation,
        run.conversation_id,
        payload.message,
        context_summary,
        request.app.state.recommendation_dispatcher,
        request.app.state.title_dispatcher,
    )
    await record_audit_event(request, "run_created", user.user_id, "success")
    return {
        "run_id": run.run_id,
        "conversation_id": run.conversation_id,
        "thread_id": run.thread_id,
        "status": "queued",
        "pending_interaction": None,
    }


async def _dispatch_start_run(
    agent_client: AgentClient,
    conversation_service: PostgresConversationService,
    audit_service: Any,
    user: InternalUserContext,
    correlation: CorrelationContext,
    conversation_id: str,
    message: str,
    context_summary: str = "",
    recommendation_dispatcher: Any | None = None,
    title_dispatcher: Any | None = None,
) -> None:
    """在响应后调用 Agent，并将调度结果映射回 PostgreSQL Run 状态。"""
    try:
        agent_run = await agent_client.start_run(
            user, correlation, conversation_id, message, context_summary
        )
        updated = await conversation_service.update_run_status(
            user.user_id, correlation.run_id, agent_run.status
        )
        if agent_run.assistant_reply:
            await conversation_service.append_assistant_message(
                user.user_id, correlation.run_id, agent_run.assistant_reply
            )
            if recommendation_dispatcher is not None:
                asyncio.create_task(
                    recommendation_dispatcher.dispatch(
                    user=user,
                    correlation=correlation,
                    conversation_id=conversation_id,
                    thread_id=correlation.thread_id,
                    answer_version=f"{correlation.run_id}-{agent_run.version}",
                    run_status=agent_run.status,
                    user_question=message,
                    assistant_reply=agent_run.assistant_reply,
                    has_pending_interaction=agent_run.pending_interaction is not None,
                    )
                )
            if title_dispatcher is not None:
                asyncio.create_task(
                    title_dispatcher.dispatch(
                        user,
                        correlation,
                        conversation_service,
                        conversation_id,
                        correlation.thread_id,
                        message,
                        _intent_json(agent_run),
                    )
                )
        outcome = "success" if updated is not None else "failed"
        await audit_service.record(
            "run_dispatched",
            user.user_id,
            outcome,
            correlation.trace_id,
            correlation.request_id,
            correlation.run_id,
            correlation.thread_id,
        )
    except Exception:
        await conversation_service.update_run_status(user.user_id, correlation.run_id, "failed")
        await audit_service.record(
            "run_dispatch_failed",
            user.user_id,
            "failed",
            correlation.trace_id,
            correlation.request_id,
            correlation.run_id,
            correlation.thread_id,
        )


async def _dispatch_resume_run(
    agent_client: AgentClient,
    conversation_service: PostgresConversationService,
    audit_service: Any,
    user: InternalUserContext,
    correlation: CorrelationContext,
    conversation_id: str,
    thread_id: str,
    task_brief: str,
    context_summary: str,
    resume_decision: dict[str, object] | None = None,
    confirmation_token: str | None = None,
    quick_action: str | None = None,
    recommendation_dispatcher: Any | None = None,
    title_dispatcher: Any | None = None,
) -> None:
    """在响应后恢复 Run 执行，并把最终状态、助手回复写回 PostgreSQL。"""
    try:
        agent_run = await agent_client.resume_run(
            user,
            correlation,
            conversation_id,
            task_brief,
            context_summary,
            resume_decision=resume_decision,
            confirmation_token=confirmation_token,
            quick_action=quick_action,
        )
        updated = await conversation_service.update_run_status(
            user.user_id, correlation.run_id, agent_run.status
        )
        if agent_run.assistant_reply:
            await conversation_service.append_assistant_message(
                user.user_id, correlation.run_id, agent_run.assistant_reply
            )
            if recommendation_dispatcher is not None:
                asyncio.create_task(
                    recommendation_dispatcher.dispatch(
                        user=user,
                        correlation=correlation,
                        conversation_id=conversation_id,
                        thread_id=thread_id,
                        answer_version=f"{correlation.run_id}-{agent_run.version}",
                        run_status=agent_run.status,
                        user_question=task_brief,
                        assistant_reply=agent_run.assistant_reply,
                        has_pending_interaction=agent_run.pending_interaction is not None,
                    )
                )
            if title_dispatcher is not None:
                asyncio.create_task(
                    title_dispatcher.dispatch(
                        user,
                        correlation,
                        conversation_service,
                        conversation_id,
                        thread_id,
                        task_brief,
                        _intent_json(agent_run),
                    )
                )
        outcome = "success" if updated is not None else "failed"
        await audit_service.record(
            "run_resumed",
            user.user_id,
            outcome,
            correlation.trace_id,
            correlation.request_id,
            correlation.run_id,
            correlation.thread_id,
        )
    except AgentCommandError as error:
        await conversation_service.update_run_status(
            user.user_id, correlation.run_id, "failed"
        )
        await audit_service.record(
            "run_resume_failed",
            user.user_id,
            "failed",
            correlation.trace_id,
            correlation.request_id,
            correlation.run_id,
            correlation.thread_id,
        )
        logger.warning(
            "run_resume_failed trace_id=%s run_id=%s error_code=%s",
            correlation.trace_id,
            correlation.run_id,
            error.code,
        )
    except Exception:
        await conversation_service.update_run_status(
            user.user_id, correlation.run_id, "failed"
        )
        await audit_service.record(
            "run_resume_failed",
            user.user_id,
            "failed",
            correlation.trace_id,
            correlation.request_id,
            correlation.run_id,
            correlation.thread_id,
        )


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str, request: Request, user: AuthenticatedUser = Depends(get_current_user)
) -> dict[str, object]:
    """读取当前用户自己的 Run 元数据。"""
    run = await _conversation_service(request).get_run(user.user_id, run_id)
    if run is None:
        raise ServiceError("run_not_found", "运行不存在或无权访问", False, 404)
    correlation = _set_run_correlation(request, run.run_id, run.thread_id)
    try:
        pending = await _agent_client(request).get_pending_interaction(
            _internal_user(user), correlation, run.conversation_id
        )
    except AgentCommandError as error:
        raise _agent_command_error(error) from error
    diagnostics = await _load_run_diagnostics(
        request, user, run.conversation_id, run.thread_id, correlation
    )
    return {
        "run_id": run.run_id,
        "conversation_id": run.conversation_id,
        "thread_id": run.thread_id,
        "status": run.status,
        "pending_interaction": pending,
        "diagnostics": diagnostics,
    }


@router.post("/runs/{run_id}/resume", dependencies=[Depends(require_csrf)])
async def resume_run(
    run_id: str,
    payload: ResumeRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    """加密保存用户补充消息后恢复同一 Run；检查点过期时不会创建新的执行链。"""
    conversation_service = _conversation_service(request)
    existing_run = await conversation_service.get_run(user.user_id, run_id)
    if existing_run is None:
        raise ServiceError("run_not_found", "运行不存在或无权访问", False, 404)
    context_summary = await conversation_service.get_desensitized_context_summary(
        user.user_id, existing_run.conversation_id
    )
    if payload.message and payload.message.strip():
        run = await conversation_service.append_user_message(
            user.user_id, run_id, payload.message.strip()
        )
        if run is None:
            raise ServiceError("run_not_found", "运行不存在或无权访问", False, 404)
    else:
        run = existing_run
    correlation = _set_run_correlation(request, run.run_id, run.thread_id)
    background_tasks.add_task(
        _dispatch_resume_run,
        _agent_client(request),
        _conversation_service(request),
        request.app.state.audit_service,
        _internal_user(user),
        correlation,
        run.conversation_id,
        run.thread_id,
        payload.message or "用户已提交确认决定",
        context_summary,
        (
            {
                "interaction_id": payload.interaction_id,
                "decision": payload.decision,
                "message": payload.message,
                "edited_args": payload.edited_args,
            }
            if payload.kind == "decision"
            else None
        ),
        payload.confirmation_token,
        payload.quick_action if payload.kind == "quick_action" else None,
        request.app.state.recommendation_dispatcher,
        getattr(request.app.state, "title_dispatcher", None),
    )
    await record_audit_event(request, "run_resumed", user.user_id, "success")
    return {
        "run_id": run.run_id,
        "conversation_id": run.conversation_id,
        "thread_id": run.thread_id,
        "status": "running",
        "pending_interaction": None,
        "assistant_reply": None,
        "diagnostics": {},
    }


@router.get("/runs/{run_id}/events", response_class=EventSourceResponse)
async def recommendation_events(
    run_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> AsyncIterator[ServerSentEvent]:
    """鉴权后以 SSE 增量转发当前 Run 的事件，并支持 Last-Event-ID 补发。

    该处理函数必须是 async generator：FastAPI 的 SSE 路由要求处理函数本身产出事件。
    """
    run = await _conversation_service(request).get_run(user.user_id, run_id)
    if run is None:
        raise ServiceError("run_not_found", "运行不存在或无权访问", False, 404)
    correlation = _set_run_correlation(request, run.run_id, run.thread_id)
    cursor = last_event_id
    started_at = time.monotonic()
    # 在有限窗口内持续轮询并逐批下发，保证步骤与回答分片按发生顺序逐条出现。
    while time.monotonic() - started_at < _EVENT_STREAM_WINDOW_SECONDS:
        try:
            events = await _agent_client(request).list_recommendation_events(
                _internal_user(user), correlation, run.conversation_id, run.thread_id, cursor
            )
        except AgentCommandError:
            yield ServerSentEvent(
                event="error",
                data={"code": "recommendation_events_unavailable", "retryable": True},
            )
            return
        finished = False
        if events:
            for event in events:
                event_id = event.get("stream_id") or event.get("event_id")
                cursor = str(event_id) if event_id else cursor
                event_type = str(event.get("type") or "message")
                yield ServerSentEvent(
                    event=event_type,
                    id=cursor,
                    data=event.get("data", {"items": []}),
                )
                if event_type in _EVENT_STREAM_END_TYPES:
                    finished = True
        if finished:
            return
        elapsed = time.monotonic() - started_at
        interval = (
            _EVENT_POLL_INTERVAL_SECONDS
            if elapsed < _EVENT_POLL_ACTIVE_SECONDS
            else _EVENT_POLL_IDLE_INTERVAL_SECONDS
        )
        await asyncio.sleep(interval)


@router.delete("/conversations/{conversation_id}", dependencies=[Depends(require_csrf)])
async def delete_conversation(
    conversation_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    """软删除当前用户自己的会话；越权或不存在时返回 404 且不泄露存在性。"""
    deleted = await _conversation_service(request).delete_conversation(
        user.user_id, conversation_id
    )
    if not deleted:
        await record_audit_event(request, "conversation_delete_denied", user.user_id, "denied")
        raise ServiceError("conversation_not_found", "会话不存在或无权访问", False, 404)
    await record_audit_event(request, "conversation_deleted", user.user_id, "success")
    return {"conversation_id": conversation_id, "status": "deleted"}


@router.put("/conversations/{conversation_id}/title", dependencies=[Depends(require_csrf)])
async def rename_conversation(
    conversation_id: str,
    payload: RenameConversationRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    """重命名当前用户自己的会话标题，并写入审计。"""
    title = await _conversation_service(request).rename_conversation(
        user.user_id, conversation_id, payload.title
    )
    if title is None:
        await record_audit_event(request, "conversation_rename_denied", user.user_id, "denied")
        raise ServiceError("conversation_not_found", "会话不存在或无权访问", False, 404)
    await record_audit_event(request, "conversation_renamed", user.user_id, "success")
    return {"conversation_id": conversation_id, "title": title}


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    """返回当前用户会话中的脱敏消息正文，供工作台恢复显示。"""
    messages = await _conversation_service(request).list_messages(
        user.user_id, conversation_id
    )
    return {
        "conversation_id": conversation_id,
        "messages": [
            {
                "message_id": item.message_id,
                "run_id": item.run_id,
                "role": item.role,
                "content": item.content,
                "created_at": item.created_at.isoformat(),
                "feedback": item.feedback,
            }
            for item in messages
        ],
    }


@router.put(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    dependencies=[Depends(require_csrf)],
)
async def set_message_feedback(
    conversation_id: str,
    message_id: str,
    payload: MessageFeedbackRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    """对当前用户会话内的助手消息写入或清除点赞/点踩反馈。"""
    feedback = await _conversation_service(request).set_message_feedback(
        user.user_id, conversation_id, message_id, payload.feedback
    )
    if payload.feedback is not None and feedback is None:
        raise ServiceError("message_not_found", "消息不存在或无权访问", False, 404)
    await record_audit_event(request, "message_feedback_set", user.user_id, "success")
    return {"message_id": message_id, "feedback": feedback}


@router.post("/runs/{run_id}/cancel", dependencies=[Depends(require_csrf)])
async def cancel_run(
    run_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    """取消当前用户拥有的 Run，并同步 PostgreSQL 展示状态与 Redis 检查点。"""
    run = await _conversation_service(request).get_run(user.user_id, run_id)
    if run is None:
        raise ServiceError("run_not_found", "运行不存在或无权访问", False, 404)
    correlation = _set_run_correlation(request, run.run_id, run.thread_id)
    try:
        agent_run = await _agent_client(request).cancel_run(
            _internal_user(user), correlation, run.conversation_id
        )
    except AgentCommandError as error:
        await record_audit_event(request, "run_cancel_failed", user.user_id, "failed")
        raise _agent_command_error(error) from error
    updated_run = await _conversation_service(request).update_run_status(
        user.user_id, run_id, agent_run.status
    )
    if updated_run is None:
        raise ServiceError("run_not_found", "运行不存在或无权访问", False, 404)
    await record_audit_event(request, "run_cancelled", user.user_id, "success")
    return _run_payload(updated_run)


def _run_payload(
    run: RunSummary,
    pending_interaction: dict[str, object] | None = None,
    assistant_reply: str | None = None,
    diagnostics: dict[str, object] | None = None,
) -> dict[str, object]:
    """统一输出浏览器可见的 Run 元数据，不返回加密消息或 Agent 内部检查点。"""
    return {
        "run_id": run.run_id,
        "conversation_id": run.conversation_id,
        "thread_id": run.thread_id,
        "status": run.status,
        "pending_interaction": pending_interaction,
        "assistant_reply": assistant_reply,
        "diagnostics": diagnostics or {},
    }


async def _load_run_diagnostics(
    request: Request,
    user: AuthenticatedUser,
    conversation_id: str,
    thread_id: str,
    correlation: CorrelationContext,
) -> dict[str, object]:
    """从安全事件流聚合当前 Run 诊断快照，不读取 Agent 内部状态或提示词。"""
    try:
        events = await _agent_client(request).list_recommendation_events(
            _internal_user(user), correlation, conversation_id, thread_id
        )
    except AgentCommandError:
        return {"stages": [], "events_unavailable": True}
    stages: list[dict[str, object]] = []
    route: dict[str, object] = {}
    master: dict[str, object] = {}
    sub_agents: dict[str, dict[str, object]] = {}
    recommendation: dict[str, object] = {}
    knowledge_search: dict[str, object] = {}
    token_usage: dict[str, object] = {}
    for event in events:
        event_type = str(event.get("type") or "")
        data = event.get("data")
        safe_data = data if isinstance(data, dict) else {}
        if event_type == "route_selected":
            route = dict(safe_data)
        if event_type in {"master_started", "master_completed"}:
            master = {"event_type": event_type, **safe_data}
        if event_type in {"sub_agent_started", "sub_agent_completed"}:
            agent_name = str(safe_data.get("agent") or "")
            if agent_name:
                sub_agents[agent_name] = {"event_type": event_type, **safe_data}
        if event_type.startswith("recommendation_"):
            recommendation = dict(safe_data)
        if event_type.startswith("knowledge_search_"):
            knowledge_search = dict(safe_data)
        if event_type == "token_usage":
            token_usage = dict(safe_data)
        if event_type in {
            "intent_recognition", "query_rewrite", "route_selected", "master_started",
            "master_completed", "sub_agent_started", "sub_agent_completed", "tool_summary",
            "run_error", "recommendation_started", "recommendation_completed",
            "knowledge_search_started", "knowledge_search_completed", "knowledge_search_failed",
            "interrupted", "token_usage", "tool_started", "tool_completed",
            "result_card", "run_started",
        }:
            stages.append({
                "type": event_type,
                "timestamp": event.get("timestamp"),
                "data": safe_data,
            })
    return {
        "stages": stages,
        "route": route,
        "master": master,
        "sub_agents": list(sub_agents.values()),
        "recommendation": recommendation,
        "knowledge_search": knowledge_search,
        "token_usage": token_usage,
    }
