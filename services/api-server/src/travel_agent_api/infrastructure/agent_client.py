# 本文件定义 API Server 访问 Agent Server 的内部命令客户端。
# 定义内部用户上下文、命令异常与异步客户端。
# 客户端通过 Docker Secret 发起停止记忆命令。
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx

from travel_agent_api.core.correlation import CorrelationContext

_MINIMUM_TOKEN_LENGTH = 32
_QUERY_TIMEOUT_SECONDS = 5.0
_RUN_COMMAND_TIMEOUT_SECONDS = 600.0


class AgentCommandError(RuntimeError):
    """表示 Agent 内部命令无法安全完成，携带可由 API 层映射的稳定错误信息。"""

    def __init__(self, code: str, retryable: bool) -> None:
        """创建不包含下游正文、密钥或实现细节的内部调用异常。"""
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class InternalUserContext:
    """保存 API 从受信任 Cookie 会话取得的用户、角色和隐私状态。"""

    user_id: str
    role: str
    privacy_status: str

    def to_payload(self) -> dict[str, str]:
        """返回符合内部命令契约的用户上下文，不包含浏览器原始字段。"""
        return {
            "user_id": self.user_id,
            "role": self.role,
            "privacy_status": self.privacy_status,
        }

    def to_headers(self) -> dict[str, str]:
        """返回供 Agent 二次比对的内部身份头。"""
        return {
            "X-Internal-User-Id": self.user_id,
            "X-Internal-User-Role": self.role,
            "X-Internal-Privacy-Status": self.privacy_status,
        }


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """表示 Agent 已接受的运行状态与检查点版本，不暴露任务正文或内部状态载荷。"""

    status: str
    conversation_id: str
    run_id: str
    thread_id: str
    version: int
    pending_interaction: dict[str, Any] | None = None
    assistant_reply: str | None = None
    diagnostics: dict[str, Any] | None = None
    intent_code: str | None = None
    intent_source: str | None = None


class AgentClient:
    """通过仅内部 Docker 网络和 Docker Secret 调用 Agent 命令，不暴露给浏览器。"""

    def __init__(
        self,
        base_url: str,
        token_file: str,
        transport: httpx.AsyncBaseTransport | None = None,
        run_command_timeout_seconds: float = _RUN_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        """保存内部地址、Secret 文件路径和可注入测试传输层。"""
        self._base_url = base_url.rstrip("/")
        self._token_file = token_file
        self._transport = transport
        self._run_command_timeout_seconds = run_command_timeout_seconds

    async def suspend_user_memory(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        reason: str = "account_deletion_requested",
    ) -> dict[str, Any]:
        """请求 Agent 停止该用户的长期记忆注入；仅接受注销或删除语义。"""
        token = _read_internal_token(self._token_file)
        headers = {
            "Authorization": f"Bearer {token}",
            **correlation.request_headers(),
            **user.to_headers(),
        }
        payload = {
            "command_version": "v1",
            "command": "suspend_user_memory",
            "request_id": correlation.request_id,
            "trace_id": correlation.trace_id,
            "user": user.to_payload(),
            "reason": reason,
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(5.0, connect=1.0),
                transport=self._transport,
            ) as client:
                response = await client.post(
                    "/internal/v1/commands/suspend-user-memory",
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as error:
            raise AgentCommandError("agent_service_unavailable", True) from error
        if response.status_code >= 500:
            raise AgentCommandError("agent_command_failed", True)
        if response.status_code >= 400:
            raise AgentCommandError("agent_command_rejected", False)
        return cast(dict[str, Any], response.json())

    async def start_run(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_id: str,
        task_brief: str,
        context_summary: str = "",
    ) -> AgentRunResult:
        """请求 Agent 创建幂等 Redis 检查点；本方法不触发模型、工具或外部 Provider。"""
        return await self._send_run_command(
            "start", user, correlation, conversation_id, task_brief, context_summary
        )

    async def resume_run(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_id: str,
        task_brief: str,
        context_summary: str = "",
        resume_decision: dict[str, object] | None = None,
        confirmation_token: str | None = None,
        quick_action: str | None = None,
    ) -> AgentRunResult:
        """请求 Agent 使用既有线程恢复运行，补充消息仅通过受保护内部命令传递。"""
        return await self._send_run_command(
            "resume",
            user,
            correlation,
            conversation_id,
            task_brief,
            context_summary,
            resume_decision=resume_decision,
            confirmation_token=confirmation_token,
            quick_action=quick_action,
        )

    async def generate_recommendations(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_id: str,
        thread_id: str,
        answer_version: str,
        run_status: str,
        user_question: str,
        assistant_reply: str,
        context_summary: str = "",
        has_pending_interaction: bool = False,
    ) -> dict[str, Any]:
        """在主答案完成后请求 Agent 异步生成推荐问题。"""
        payload: dict[str, object] = {
            "command_version": "v1",
            "command": "recommendations",
            "request_id": correlation.request_id,
            "trace_id": correlation.trace_id,
            "user": user.to_payload(),
            "conversation_id": conversation_id,
            "run_id": correlation.run_id,
            "thread_id": thread_id,
            "answer_version": answer_version,
            "run_status": run_status,
            "user_question": user_question,
            "assistant_reply": assistant_reply,
            "context_summary": context_summary[:2000],
            "has_pending_interaction": has_pending_interaction,
        }
        response = await self._post_command(
            "/internal/v1/recommendations", user, correlation, payload
        )
        return cast(dict[str, Any], response.json())

    async def list_recommendation_events(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_id: str,
        thread_id: str,
        last_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """读取 Agent Redis 中当前 Run 的推荐事件。"""
        token = _read_internal_token(self._token_file)
        headers = {
            "Authorization": f"Bearer {token}",
            **correlation.request_headers(),
            **user.to_headers(),
        }
        params: dict[str, str] = {
            "conversation_id": conversation_id,
            "thread_id": thread_id,
        }
        if last_event_id:
            params["last_event_id"] = last_event_id
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(5.0, connect=1.0),
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"/internal/v1/recommendations/{correlation.run_id}/events",
                    headers=headers,
                    params=params,
                )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise AgentCommandError("agent_service_unavailable", True) from error
        events = body.get("events") if isinstance(body, dict) else None
        if not isinstance(events, list):
            raise AgentCommandError("agent_command_invalid_response", True)
        return [cast(dict[str, Any], event) for event in events if isinstance(event, dict)]

    async def generate_conversation_title(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_id: str,
        thread_id: str,
        user_question: str,
        intent_result_json: str,
    ) -> str | None:
        """请求 Agent 异步生成会话标题；返回 None 表示未生成或不可用。"""
        payload: dict[str, object] = {
            "command_version": "v1",
            "command": "conversation_title",
            "request_id": correlation.request_id,
            "trace_id": correlation.trace_id,
            "user": user.to_payload(),
            "conversation_id": conversation_id,
            "run_id": correlation.run_id,
            "thread_id": thread_id,
            "user_question": user_question[:8000],
            "intent_result_json": intent_result_json[:2000],
        }
        response = await self._post_command(
            "/internal/v1/conversation-title", user, correlation, payload
        )
        body = cast(dict[str, Any], response.json())
        title = body.get("title")
        return title if isinstance(title, str) and title.strip() else None

    async def cancel_run(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_id: str,
    ) -> AgentRunResult:
        """请求 Agent 停止指定 Run（与 interrupt_run 同义，保留既有命名）。"""
        return await self.interrupt_run(user, correlation, conversation_id)

    async def interrupt_run(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_id: str,
    ) -> AgentRunResult:
        """请求 Agent 中断在途执行：本地取消任务并广播到其它节点。"""
        return await self._send_run_command(
            "cancel", user, correlation, conversation_id, interrupt=True
        )

    async def _send_run_command(
        self,
        command: str,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_id: str,
        task_brief: str | None = None,
        context_summary: str = "",
        resume_decision: dict[str, object] | None = None,
        confirmation_token: str | None = None,
        quick_action: str | None = None,
        interrupt: bool = False,
    ) -> AgentRunResult:
        """按 v1 契约发送运行命令并解析受限响应，拒绝缺失或不一致的下游数据。"""
        payload: dict[str, object] = {
            "command_version": "v1",
            "command": command,
            "request_id": correlation.request_id,
            "trace_id": correlation.trace_id,
            "user": user.to_payload(),
            "conversation_id": conversation_id,
            "run_id": correlation.run_id,
            "thread_id": correlation.thread_id,
        }
        if task_brief is not None:
            payload["task_brief"] = task_brief
            payload["context_summary"] = context_summary[:2000]
        if resume_decision is not None:
            payload["resume_decision"] = resume_decision
        if quick_action is not None:
            payload["quick_action"] = quick_action
        response = await self._post_command(
            (
                f"/internal/v1/commands/runs/{correlation.run_id}/interrupt"
                if interrupt
                else f"/internal/v1/commands/runs/{command}"
            ),
            user,
            correlation,
            payload,
            extra_headers=(
                {"X-Confirmation-Token": confirmation_token}
                if confirmation_token is not None
                else None
            ),
            long_running=True,
        )
        body = cast(dict[str, Any], response.json())
        try:
            result = AgentRunResult(
                status=_required_string(body, "status"),
                conversation_id=_required_string(body, "conversation_id"),
                run_id=_required_string(body, "run_id"),
                thread_id=_required_string(body, "thread_id"),
                version=_required_positive_int(body, "version"),
                pending_interaction=(
                    cast(dict[str, Any], body["pending_interaction"])
                    if isinstance(body.get("pending_interaction"), dict)
                    else None
                ),
                assistant_reply=(
                    body["assistant_reply"]
                    if isinstance(body.get("assistant_reply"), str)
                    else None
                ),
                diagnostics=(
                    cast(dict[str, Any], body["diagnostics"])
                    if isinstance(body.get("diagnostics"), dict)
                    else None
                ),
                intent_code=(
                    body["intent_code"] if isinstance(body.get("intent_code"), str) else None
                ),
                intent_source=(
                    body["intent_source"] if isinstance(body.get("intent_source"), str) else None
                ),
            )
        except (TypeError, ValueError) as error:
            raise AgentCommandError("agent_command_invalid_response", True) from error
        if (result.conversation_id, result.run_id, result.thread_id) != (
            conversation_id,
            correlation.run_id,
            correlation.thread_id,
        ):
            raise AgentCommandError("agent_command_invalid_response", True)
        return result

    async def get_pending_interaction(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        """读取当前 Run 的待交互安全摘要。"""
        token = _read_internal_token(self._token_file)
        headers = {
            "Authorization": f"Bearer {token}",
            **correlation.request_headers(),
            **user.to_headers(),
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(5.0, connect=1.0),
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"/internal/v1/commands/runs/{correlation.run_id}/interaction",
                    headers=headers,
                    params={"conversation_id": conversation_id, "thread_id": correlation.thread_id},
                )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise AgentCommandError("agent_service_unavailable", True) from error
        interaction = body.get("pending_interaction") if isinstance(body, dict) else None
        if interaction is not None and not isinstance(interaction, dict):
            raise AgentCommandError("agent_command_invalid_response", True)
        return cast(dict[str, Any] | None, interaction)

    async def consume_hitl_token(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_id: str,
        interaction_id: str,
        confirmation_token: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> str:
        """请求 Agent 原子消费 Token，并返回用于业务写入的稳定幂等键。"""
        payload: dict[str, object] = {
            "command_version": "v1",
            "command": "consume_hitl_token",
            "request_id": correlation.request_id,
            "trace_id": correlation.trace_id,
            "user": user.to_payload(),
            "conversation_id": conversation_id,
            "run_id": correlation.run_id,
            "thread_id": correlation.thread_id,
            "interaction_id": interaction_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
        }
        response = await self._post_command(
            "/internal/v1/commands/hitl/consume",
            user,
            correlation,
            payload,
            extra_headers={
                "X-Confirmation-Token": confirmation_token,
            },
        )
        body = cast(dict[str, Any], response.json())
        return _required_string(body, "idempotency_key")

    async def list_debug_agents(
        self, user: InternalUserContext, correlation: CorrelationContext
    ) -> list[dict[str, Any]]:
        """读取可调试直达的智能体列表；仅管理员身份可调用。"""
        body = await self._debug_request(
            "GET", "/internal/v1/debug/agents", user, correlation, None
        )
        agents = body.get("agents")
        if not isinstance(agents, list):
            raise AgentCommandError("agent_command_invalid_response", True)
        return [item for item in agents if isinstance(item, dict)]

    async def debug_agent(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        agent_name: str,
        message: str,
    ) -> dict[str, Any]:
        """绕过意图识别直接把消息发给指定智能体，返回回复与待交互摘要。"""
        payload: dict[str, object] = {
            "command_version": "v1",
            "command": "debug_agent",
            "message": message,
            "context_summary": "",
            "conversation_id": f"debug_{correlation.thread_id}"[:64],
        }
        return await self._debug_request(
            "POST",
            f"/internal/v1/debug/agents/{agent_name}",
            user,
            correlation,
            payload,
        )

    async def retrieve_memory(
        self, user: InternalUserContext, correlation: CorrelationContext, query: str
    ) -> dict[str, Any]:
        """召回当前用户的长期偏好记忆；未配置记忆库时返回 available=false。"""
        return await self._debug_request(
            "POST",
            "/internal/v1/memory/retrieve",
            user,
            correlation,
            {"query": query},
        )

    async def record_memory(
        self, user: InternalUserContext, correlation: CorrelationContext, content: str
    ) -> dict[str, Any]:
        """把偏好句子写入当前用户的长期记忆。"""
        return await self._debug_request(
            "POST",
            "/internal/v1/memory/record",
            user,
            correlation,
            {"content": content},
        )

    async def parse_preferences(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        memory_text: str,
        catalog: dict[str, list[str]],
    ) -> dict[str, Any]:
        """请求把记忆原文解析为目录内的结构化偏好。"""
        return await self._debug_request(
            "POST",
            "/internal/v1/memory/preferences",
            user,
            correlation,
            {"memory_text": memory_text, "catalog": catalog},
        )

    async def _debug_request(
        self,
        method: str,
        path: str,
        user: InternalUserContext,
        correlation: CorrelationContext,
        payload: dict[str, object] | None,
    ) -> dict[str, Any]:
        """发送调试直达内部请求，并复用统一的错误收敛口径。"""
        token = _read_internal_token(self._token_file)
        headers = {
            "Authorization": f"Bearer {token}",
            **correlation.request_headers(),
            **user.to_headers(),
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._run_command_timeout_seconds, connect=2.0),
                transport=self._transport,
            ) as client:
                response = await client.request(method, path, headers=headers, json=payload)
        except httpx.HTTPError as error:
            raise AgentCommandError("agent_service_unavailable", True) from error
        if response.status_code >= 500:
            raise AgentCommandError("agent_command_failed", True)
        if response.status_code >= 400:
            raise AgentCommandError("agent_command_rejected", False)
        body = response.json()
        if not isinstance(body, dict):
            raise AgentCommandError("agent_command_invalid_response", True)
        return cast(dict[str, Any], body)

    async def _post_command(
        self,
        path: str,
        user: InternalUserContext,
        correlation: CorrelationContext,
        payload: dict[str, object],
        extra_headers: dict[str, str] | None = None,
        *,
        long_running: bool = False,
    ) -> httpx.Response:
        """读取 Docker Secret，发送内部命令，并将下游错误收敛成稳定错误码。"""
        token = _read_internal_token(self._token_file)
        headers = {
            "Authorization": f"Bearer {token}",
            **correlation.request_headers(),
            **user.to_headers(),
        }
        if extra_headers:
            headers.update(extra_headers)
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=(
                    httpx.Timeout(self._run_command_timeout_seconds, connect=2.0)
                    if long_running
                    else httpx.Timeout(_QUERY_TIMEOUT_SECONDS, connect=1.0)
                ),
                transport=self._transport,
            ) as client:
                response = await client.post(path, headers=headers, json=payload)
        except httpx.HTTPError as error:
            raise AgentCommandError("agent_service_unavailable", True) from error
        if response.status_code >= 500:
            raise AgentCommandError("agent_command_failed", True)
        if response.status_code >= 400:
            raise AgentCommandError("agent_command_rejected", False)
        return response


def _read_internal_token(token_file: str) -> str:
    """从 Docker Secret 文件读取 Token；文件异常或过短时拒绝在发起请求前失败。"""
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise AgentCommandError("internal_service_secret_unavailable", True) from error
    if len(token) < _MINIMUM_TOKEN_LENGTH:
        raise AgentCommandError("internal_service_secret_invalid", False)
    return token


def _required_string(payload: dict[str, Any], name: str) -> str:
    """读取 Agent 响应中的非空字符串字段，避免不可信响应进入 API 状态。"""
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(name)
    return value


def _required_positive_int(payload: dict[str, Any], name: str) -> int:
    """读取 Agent 响应中的正整数版本字段，拒绝布尔值或缺失值。"""
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(name)
    return value
