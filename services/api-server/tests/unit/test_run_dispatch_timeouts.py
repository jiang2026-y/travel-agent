# 文件职责：验证内部运行命令的超时分层、异步恢复受理与中断调用。
# 定义执行类长超时、查询类短超时、resume 走后台任务与 cancel 触发中断的测试。
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from travel_agent_api.core.correlation import CorrelationContext
from travel_agent_api.infrastructure.agent_client import AgentClient, InternalUserContext


def _client(tmp_path, handler, *, timeout: float = 600.0) -> AgentClient:
    """构造注入 MockTransport 的内部命令客户端。"""
    token_file = tmp_path / "api_agent_internal_token"
    token_file.write_text("t" * 32, encoding="utf-8")
    return AgentClient(
        "http://agent-server:8001",
        str(token_file),
        transport=httpx.MockTransport(handler),
        run_command_timeout_seconds=timeout,
    )


def _context() -> tuple[InternalUserContext, CorrelationContext]:
    """构造固定的内部用户与关联上下文。"""
    return (
        InternalUserContext("user_1", "user", "active"),
        CorrelationContext(
            trace_id="trace", request_id="request", run_id="run_1", thread_id="thread_1"
        ),
    )


def _run_response() -> dict[str, Any]:
    """返回最小合法运行命令响应。"""
    return {
        "status": "running",
        "conversation_id": "conv_1",
        "run_id": "run_1",
        "thread_id": "thread_1",
        "version": 1,
    }


@pytest.mark.asyncio
async def test_execution_command_uses_long_timeout(tmp_path) -> None:
    """start 命令必须使用执行类长超时，而不是查询类 5 秒预算。"""
    seen: dict[str, float] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        """记录 httpx 实际生效的读超时并返回成功响应。"""
        seen["read"] = request.extensions.get("timeout", {}).get("read", 0.0)
        return httpx.Response(200, json=_run_response())

    user, correlation = _context()
    client = _client(tmp_path, handler, timeout=600.0)
    await client.start_run(user, correlation, "conv_1", "出差申请", "")
    assert seen["read"] == pytest.approx(600.0)


@pytest.mark.asyncio
async def test_query_command_keeps_short_timeout(tmp_path) -> None:
    """待交互查询等查询类命令继续使用 5 秒预算。"""
    seen: dict[str, float] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        """记录读超时并返回空待交互。"""
        seen["read"] = request.extensions.get("timeout", {}).get("read", 0.0)
        return httpx.Response(200, json={"pending_interaction": None})

    user, correlation = _context()
    client = _client(tmp_path, handler, timeout=600.0)
    await client.get_pending_interaction(user, correlation, "conv_1")
    assert seen["read"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_cancel_targets_interrupt_endpoint(tmp_path) -> None:
    """停止生成必须命中路径式中断端点并携带 cancel 命令体。"""
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        """记录目标路径与请求体。"""
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={**_run_response(), "status": "cancelled"})

    user, correlation = _context()
    client = _client(tmp_path, handler)
    result = await client.cancel_run(user, correlation, "conv_1")

    assert seen["path"] == "/internal/v1/commands/runs/run_1/interrupt"
    assert seen["body"]["command"] == "cancel"  # type: ignore[index]
    assert result.status == "cancelled"


def test_agent_client_reads_timeout_from_settings(tmp_path) -> None:
    """设置项可覆盖默认执行类超时，避免硬编码。"""
    from travel_agent_api.core.settings import Settings

    settings = Settings.from_environment(
        {
            "TRAVEL_AGENT_ENV": "test",
            "TRAVEL_AGENT_RUN_COMMAND_TIMEOUT_SECONDS": "120",
        }
    )
    assert settings.run_command_timeout_seconds == pytest.approx(120.0)


class _FakeAgentClient:
    """返回预设运行结果的 Agent 客户端替身。"""

    def __init__(self, status: str = "completed", reply: str | None = "已为您登记") -> None:
        self.status = status
        self.reply = reply
        self.calls = 0

    async def resume_run(self, *args: Any, **kwargs: Any) -> Any:
        """记录调用并返回预设结果。"""
        del args, kwargs
        self.calls += 1
        return AgentRunResultStub(self.status, self.reply)


class AgentRunResultStub:
    """模拟 AgentRunResult 的最小字段集合。"""

    def __init__(self, status: str, reply: str | None) -> None:
        self.status = status
        self.assistant_reply = reply
        self.version = 2
        self.pending_interaction = None


class _FakeConversationService:
    """记录状态与助手消息写入的会话服务替身。"""

    def __init__(self) -> None:
        self.statuses: list[tuple[str, str, str]] = []
        self.messages: list[tuple[str, str, str]] = []

    async def update_run_status(self, user_id: str, run_id: str, status: str) -> object:
        """记录状态更新。"""
        self.statuses.append((user_id, run_id, status))
        return object()

    async def append_assistant_message(self, user_id: str, run_id: str, content: str) -> object:
        """记录助手消息。"""
        self.messages.append((user_id, run_id, content))
        return object()


class _FakeAuditService:
    """记录审计事件的替身。"""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def record(self, event_type: str, *args: Any, **kwargs: Any) -> None:
        """记录事件类型。"""
        del args, kwargs
        self.events.append(event_type)


@pytest.mark.asyncio
async def test_resume_dispatch_updates_status_and_reply_in_background() -> None:
    """异步恢复受理后，后台任务负责写回终态与助手回复。"""
    from travel_agent_api.api.routes.conversations import _dispatch_resume_run

    agent_client = _FakeAgentClient()
    conversations = _FakeConversationService()
    audit = _FakeAuditService()
    user, correlation = _context()
    await _dispatch_resume_run(
        agent_client,  # type: ignore[arg-type]
        conversations,  # type: ignore[arg-type]
        audit,
        user,
        correlation,
        "conv_1",
        "thread_1",
        "确认提交",
        "",
        None,
        None,
        None,
        None,
        None,
    )
    assert agent_client.calls == 1
    assert conversations.statuses == [("user_1", "run_1", "completed")]
    assert conversations.messages == [("user_1", "run_1", "已为您登记")]
    assert audit.events == ["run_resumed"]
