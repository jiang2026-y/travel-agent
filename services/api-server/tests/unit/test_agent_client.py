# 本文件验证 API Server 的 Agent 内部命令客户端。
# 定义 Docker Secret 读取失败、认证与关联头透传、以及下游拒绝映射的测试函数。
import asyncio
import json

import httpx
import pytest

from travel_agent_api.core.correlation import CorrelationContext
from travel_agent_api.infrastructure.agent_client import (
    AgentClient,
    AgentCommandError,
    InternalUserContext,
)


def _correlation() -> CorrelationContext:
    """创建固定关联标识，便于验证 API 到 Agent 的链路透传。"""
    return CorrelationContext(
        trace_id="trace_001",
        request_id="request_001",
        run_id="run_001",
        thread_id="thread_001",
    )


def _user() -> InternalUserContext:
    """创建来自受信任会话解析结果的内部用户上下文。"""
    return InternalUserContext("user_001", "user", "deletion_pending")


def test_agent_client_forwards_secret_identity_and_correlation(tmp_path) -> None:
    """客户端必须仅向内部地址发送 Secret、二次校验头和关联标识。"""
    token_file = tmp_path / "api_agent_internal_token"
    token_file.write_text("a" * 32, encoding="utf-8")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL(
            "http://agent-server:8001/internal/v1/commands/suspend-user-memory"
        )
        assert request.headers["authorization"] == f"Bearer {'a' * 32}"
        assert request.headers["x-internal-user-id"] == "user_001"
        assert request.headers["x-internal-user-role"] == "user"
        assert request.headers["x-internal-privacy-status"] == "deletion_pending"
        assert request.headers["x-trace-id"] == "trace_001"
        assert request.headers["x-request-id"] == "request_001"
        assert request.headers["x-run-id"] == "run_001"
        assert request.headers["x-thread-id"] == "thread_001"
        assert json.loads(request.content) == {
            "command_version": "v1",
            "command": "suspend_user_memory",
            "request_id": "request_001",
            "trace_id": "trace_001",
            "user": {
                "user_id": "user_001",
                "role": "user",
                "privacy_status": "deletion_pending",
            },
            "reason": "account_deletion_requested",
        }
        return httpx.Response(
            200,
            json={
                "command": "suspend_user_memory",
                "status": "memory_suspended",
                "user_id": "user_001",
                "privacy_status": "deletion_pending",
            },
        )

    client = AgentClient(
        "http://agent-server:8001",
        str(token_file),
        httpx.MockTransport(handler),
    )
    result = asyncio.run(client.suspend_user_memory(_user(), _correlation()))

    assert result["status"] == "memory_suspended"


def test_agent_client_denies_missing_secret_before_network_call(tmp_path) -> None:
    """Docker Secret 缺失时必须在 API 边界安全失败，不能退化为无认证调用。"""
    client = AgentClient("http://agent-server:8001", str(tmp_path / "missing"))

    with pytest.raises(AgentCommandError, match="internal_service_secret_unavailable"):
        asyncio.run(client.suspend_user_memory(_user(), _correlation()))


def test_agent_client_maps_agent_rejection_without_exposing_body(tmp_path) -> None:
    """Agent 拒绝命令时客户端只暴露稳定错误码，不转发下游错误正文。"""
    token_file = tmp_path / "api_agent_internal_token"
    token_file.write_text("a" * 32, encoding="utf-8")
    client = AgentClient(
        "http://agent-server:8001",
        str(token_file),
        httpx.MockTransport(lambda request: httpx.Response(403, json={"secret": "hidden"})),
    )

    with pytest.raises(AgentCommandError, match="agent_command_rejected"):
        asyncio.run(client.suspend_user_memory(_user(), _correlation()))


def test_agent_client_sends_run_start_contract(tmp_path) -> None:
    """Run 启动命令应透传完整关联标识并仅解析受限的检查点响应字段。"""
    token_file = tmp_path / "api_agent_internal_token"
    token_file.write_text("a" * 32, encoding="utf-8")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/v1/commands/runs/start"
        assert json.loads(request.content) == {
            "command_version": "v1",
            "command": "start",
            "request_id": "request_001",
            "trace_id": "trace_001",
            "user": {"user_id": "user_001", "role": "user", "privacy_status": "active"},
            "conversation_id": "conversation_001",
            "run_id": "run_001",
            "thread_id": "thread_001",
            "task_brief": "查询航班",
            "context_summary": "",
        }
        return httpx.Response(
            200,
            json={
                "command": "start",
                "status": "running",
                "conversation_id": "conversation_001",
                "run_id": "run_001",
                "thread_id": "thread_001",
                "version": 2,
            },
        )

    client = AgentClient(
        "http://agent-server:8001", str(token_file), httpx.MockTransport(handler)
    )
    result = asyncio.run(
        client.start_run(
            InternalUserContext("user_001", "user", "active"),
            _correlation(),
            "conversation_001",
            "查询航班",
        )
    )

    assert result.status == "running"
    assert result.version == 2
