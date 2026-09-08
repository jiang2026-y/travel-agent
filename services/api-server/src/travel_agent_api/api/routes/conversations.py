# 本文件提供持久化会话和 Run 的最小 REST 路由。
# 定义 list_conversations、start_run 与 get_run，分别查询会话、原子创建初始 Run 和查询其状态。
from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Request
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


class TaskRequest(BaseModel):
    """定义新会话首条消息，不接受额外字段。"""

    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8000)


class ResumeRequest(BaseModel):
    """校验普通补充消息或结构化 HITL 恢复决定，禁止将 Token 写入消息表。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["message", "decision"] = "message"
    message: str | None = Field(default=None, max_length=8000)
    interaction_id: str | None = Field(default=None, min_length=1, max_length=128)
    decision: Literal["approve", "reject", "edit", "respond"] | None = None
    confirmation_token: str | None = Field(default=None, min_length=20, max_length=512)
    edited_args: dict[str, object] | None = None

    @model_validator(mode="after")
    def validate_kind(self) -> ResumeRequest:
        """确保普通消息与 HITL 恢复决定的必填字段互不混淆。"""
        if self.kind == "message" and (not self.message or not self.message.strip()):
            raise ValueError("resume_message_required")
        if self.kind == "decision":
            if not self.interaction_id or self.decision is None:
                raise ValueError("resume_decision_required")
            if self.decision in {"approve", "reject", "edit"} and not self.confirmation_token:
                raise ValueError("confirmation_token_required")
            if self.decision == "respond" and not (self.message and self.message.strip()):
                raise ValueError("resume_message_required")
            if self.decision == "edit" and not self.edited_args and not (
                self.message and self.message.strip()
            ):
                raise ValueError("resume_message_required")
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
    """创建加密初始消息和 queued Run；Agent 调度将在下一阶段接入。"""
    run = await _conversation_service(request).start_run(
        user.user_id, payload.message, get_correlation_context(request)
    )
    correlation = _set_run_correlation(request, run.run_id, run.thread_id)
    background_tasks.add_task(
        _dispatch_start_run,
        _agent_client(request),
        _conversation_service(request),
        request.app.state.audit_service,
        _internal_user(user),
        correlation,
        run.conversation_id,
        payload.message,
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
) -> None:
    """在响应后调用 Agent，并将调度结果映射回 PostgreSQL Run 状态。"""
    try:
        agent_run = await agent_client.start_run(
            user, correlation, conversation_id, message, ""
        )
        updated = await conversation_service.update_run_status(
            user.user_id, correlation.run_id, agent_run.status
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
    return {
        "run_id": run.run_id,
        "conversation_id": run.conversation_id,
        "thread_id": run.thread_id,
        "status": run.status,
        "pending_interaction": pending,
    }


@router.post("/runs/{run_id}/resume", dependencies=[Depends(require_csrf)])
async def resume_run(
    run_id: str,
    payload: ResumeRequest,
    request: Request,
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
    try:
        agent_run = await _agent_client(request).resume_run(
            _internal_user(user),
            correlation,
            run.conversation_id,
            payload.message or "用户已提交确认决定",
            context_summary,
            resume_decision=(
                {
                    "interaction_id": payload.interaction_id,
                    "decision": payload.decision,
                    "message": payload.message,
                    "edited_args": payload.edited_args,
                }
                if payload.kind == "decision"
                else None
            ),
            confirmation_token=payload.confirmation_token,
        )
    except AgentCommandError as error:
        await record_audit_event(request, "run_resume_failed", user.user_id, "failed")
        raise _agent_command_error(error) from error
    updated_run = await _conversation_service(request).update_run_status(
        user.user_id, run_id, agent_run.status
    )
    if updated_run is None:
        raise ServiceError("run_not_found", "运行不存在或无权访问", False, 404)
    await record_audit_event(request, "run_resumed", user.user_id, "success")
    return _run_payload(updated_run, agent_run.pending_interaction)


@router.post("/runs/{run_id}/cancel", dependencies=[Depends(require_csrf)])
async def cancel_run(
    run_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, str]:
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
    run: RunSummary, pending_interaction: dict[str, object] | None = None
) -> dict[str, object]:
    """统一输出浏览器可见的 Run 元数据，不返回加密消息或 Agent 内部检查点。"""
    return {
        "run_id": run.run_id,
        "conversation_id": run.conversation_id,
        "thread_id": run.thread_id,
        "status": run.status,
        "pending_interaction": pending_interaction,
    }
