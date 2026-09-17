# 文件职责：校验行程管理 Agent 调用内部差旅接口时的稳定错误码提取与降级行为。
# 定义错误信封解析、传输失败可重试标记与成功响应解析测试。
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.agents.itinerary_manage import client as client_module
from travel_agent_agent.agents.itinerary_manage.client import (
    TravelManageApiClient,
    TravelManageApiError,
    _extract_error_code,
)


class _FakeAsyncClient:
    """httpx.AsyncClient 的最小替身，按预设结果返回响应或抛出异常。"""

    def __init__(self, outcome: object) -> None:
        """保存本次调用要模拟的响应或异常。"""
        self.outcome = outcome
        self.requests: list[tuple[str, str]] = []

    async def __aenter__(self) -> _FakeAsyncClient:
        """进入异步上下文时返回自身。"""
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        """退出异步上下文时不吞异常。"""
        del exc_info
        return False

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """记录调用路径并按预设结果返回响应或抛出异常。"""
        del kwargs
        self.requests.append((method, path))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _client(tmp_path) -> TravelManageApiClient:
    """构造带临时内部 Token 的差旅接口客户端。"""
    token_file = tmp_path / "api_agent_internal_token"
    token_file.write_text("t" * 32, encoding="utf-8")
    return TravelManageApiClient(
        "http://api-server:8000",
        str(token_file),
        AgentContext(trace_id="trace", conversation_id="conv_1", user_id="user_1"),
    )


def _install(monkeypatch, outcome: object) -> _FakeAsyncClient:
    """把模块内 httpx.AsyncClient 替换为返回预设结果的替身。"""
    fake = _FakeAsyncClient(outcome)
    monkeypatch.setattr(client_module.httpx, "AsyncClient", lambda **kwargs: fake)
    return fake


def test_error_envelope_code_is_propagated(tmp_path, monkeypatch) -> None:
    """内部错误信封中的稳定错误码应透传为异常 code，并保留上游状态码。"""
    _install(
        monkeypatch,
        httpx.Response(404, json={"error": {"code": "travel_order_not_found"}}),
    )
    client = _client(tmp_path)

    with pytest.raises(TravelManageApiError) as captured:
        asyncio.run(client.request("GET", "/internal/v1/travel-orders/order_1"))

    assert captured.value.code == "travel_order_not_found"
    assert captured.value.status_code == 404
    assert captured.value.retryable is False


def test_non_json_error_body_falls_back_to_generic_code(tmp_path, monkeypatch) -> None:
    """上游返回非 JSON 错误正文时只能给出通用错误码，绝不回显上游正文。"""
    _install(monkeypatch, httpx.Response(500, text="<html>upstream boom</html>"))
    client = _client(tmp_path)

    with pytest.raises(TravelManageApiError) as captured:
        asyncio.run(client.request("GET", "/internal/v1/travel-orders"))

    assert captured.value.code == "travel_api_unavailable"
    assert "upstream" not in str(captured.value)


def test_transport_failure_is_retryable(tmp_path, monkeypatch) -> None:
    """连接失败属于可重试故障，必须带上可重试标记供模型提示用户稍后再试。"""
    _install(monkeypatch, httpx.ConnectError("connect failed"))
    client = _client(tmp_path)

    with pytest.raises(TravelManageApiError) as captured:
        asyncio.run(client.request("GET", "/internal/v1/travel-orders"))

    assert captured.value.code == "travel_api_unavailable"
    assert captured.value.retryable is True


def test_successful_response_returns_dictionary(tmp_path, monkeypatch) -> None:
    """成功响应应原样返回字典，供上层工具读取字段。"""
    _install(monkeypatch, httpx.Response(200, json={"order_id": "order_1"}))
    client = _client(tmp_path)

    result = asyncio.run(client.request("GET", "/internal/v1/travel-orders/order_1"))

    assert result == {"order_id": "order_1"}


def test_extract_error_code_handles_missing_and_long_envelopes() -> None:
    """错误码缺失时回退通用码，超长错误码被截断以保持稳定契约。"""
    assert _extract_error_code(httpx.Response(400, json={"error": {}})) == (
        "travel_api_unavailable"
    )
    assert _extract_error_code(httpx.Response(400, json={"detail": "nope"})) == (
        "travel_api_unavailable"
    )
    long_code = "x" * 100
    truncated = _extract_error_code(httpx.Response(400, json={"error": {"code": long_code}}))
    assert truncated == "x" * 64
