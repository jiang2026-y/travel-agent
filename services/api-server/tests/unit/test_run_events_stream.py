# 文件职责：验证 Run 事件 SSE 端点能增量推送事件（修复"回复要等下一条消息才出现"回归）。
# 定义增量推送测试与事件字段透传测试，使用替身会话服务与替身 Agent 客户端。
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from travel_agent_api.api.routes import conversations
from travel_agent_api.core.correlation import CorrelationContext
from travel_agent_api.infrastructure.agent_client import AgentCommandError
from travel_agent_api.main import create_app
from travel_agent_api.persistence.services import RunSummary


class _ConversationService:
    """只实现 SSE 路由所需查询的会话服务替身。"""

    async def get_run(self, user_id: str, run_id: str) -> RunSummary | None:
        """返回固定 Run 摘要，任意用户均可读以简化用例。"""
        del user_id
        return RunSummary(run_id, "conv_1", "thread_1", "running")


class _ScriptedAgentClient:
    """按脚本逐批返回事件的 Agent 客户端替身，用于验证增量推送。"""

    def __init__(self, batches: list[list[dict[str, Any]]], *, delay: float = 0.0) -> None:
        """保存事件批次与每批之间的延时。"""
        self.batches = batches
        self.delay = delay
        self.calls = 0

    async def list_recommendation_events(
        self,
        user: Any,
        correlation: Any,
        conversation_id: str,
        thread_id: str,
        last_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """返回当前批次；批次耗尽后一直返回空列表。"""
        del user, correlation, conversation_id, thread_id, last_event_id
        if self.delay:
            await asyncio.sleep(self.delay)
        index = self.calls
        self.calls += 1
        return self.batches[index] if index < len(self.batches) else []


class _FailingAgentClient:
    """始终失败的 Agent 客户端替身。"""

    async def list_recommendation_events(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
        """抛出稳定内部错误。"""
        raise AgentCommandError("agent_service_unavailable", True)


def _event(event_type: str, stream_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """构造一条与 Agent 事件存储一致的事件。"""
    return {"type": event_type, "stream_id": stream_id, "data": data}


def _route_request(app: Any, agent_client: Any) -> SimpleNamespace:
    """构造仅暴露路由依赖字段的替身请求。"""
    state = SimpleNamespace(
        correlation_context=CorrelationContext("trace", "request", "run_1", "thread_1"),
        conversation_service=_ConversationService(),
        agent_client=agent_client,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), state=state)


@pytest.mark.asyncio
async def test_run_events_stream_delivers_batches_progressively(monkeypatch) -> None:
    """同一连接内应按发生顺序逐批下发事件，直到本轮给出结论才结束。"""
    monkeypatch.setattr(conversations, "_EVENT_POLL_INTERVAL_SECONDS", 0.01)
    started = _event("run_started", "1-0", {"conversation_id": "conv_1", "status": "running"})
    reply = _event("assistant_message", "2-0", {"content": "好消息"})
    client = _ScriptedAgentClient([[], [started], [reply]])
    request = _route_request(None, client)

    collected = [
        item async for item in conversations.recommendation_events("run_1", request, _user(), None)
    ]

    assert [(item.event, item.id) for item in collected] == [
        ("run_started", "1-0"),
        ("assistant_message", "2-0"),
    ]
    assert collected[1].data == {"content": "好消息"}
    # 三批调用说明连接在第一批之后没有立刻关闭，而是继续等待后续事件。
    assert client.calls == 3


@pytest.mark.asyncio
async def test_run_events_stream_stops_at_window_limit(monkeypatch) -> None:
    """长时间没有任何事件时连接会按总窗口退出，交给浏览器带游标重连。"""
    monkeypatch.setattr(conversations, "_EVENT_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(conversations, "_EVENT_STREAM_WINDOW_SECONDS", 0.03)
    client = _ScriptedAgentClient([[]])

    collected = [
        item
        async for item in conversations.recommendation_events(
            "run_1", _route_request(None, client), _user(), None
        )
    ]

    assert collected == []


@pytest.mark.asyncio
async def test_run_events_stream_reports_unavailable(monkeypatch) -> None:
    """Agent 事件不可用时返回稳定 error 事件并结束，不抛出到浏览器。"""
    monkeypatch.setattr(conversations, "_EVENT_POLL_INTERVAL_SECONDS", 0.01)
    request = _route_request(None, _FailingAgentClient())

    generator = conversations.recommendation_events("run_1", request, _user(), None)
    collected = [item async for item in generator]

    assert len(collected) == 1
    assert collected[0].event == "error"
    assert collected[0].data == {
        "code": "recommendation_events_unavailable",
        "retryable": True,
    }


def test_run_events_route_streams_after_login() -> None:
    """通过真实 HTTP 路径访问时返回 text/event-stream，且不再是协程异常。"""
    app = create_app()
    app.state.conversation_service = _ConversationService()
    app.state.agent_client = _ScriptedAgentClient(
        [[_event("assistant_message", "1-0", {"content": "ok"})]]
    )
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"account": "travel.user", "password": "user-password"},
        )
        assert login.status_code == 200
        response = client.get("/api/v1/runs/run_1/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: assistant_message" in response.text
    assert 'data: {"content": "ok"}' in response.text


def _user() -> Any:
    """构造普通用户身份。"""
    from travel_agent_api.application.auth_service import AuthenticatedUser

    return AuthenticatedUser(user_id="user_001", account="travel.user", role="user")


@pytest.mark.asyncio
async def test_diagnostics_snapshot_includes_result_card_event(monkeypatch) -> None:
    """诊断快照白名单必须包含 result_card，否则前端拿不到结果卡片。"""
    card = {
        "kind": "train",
        "title": "火车票搜索结果",
        "source": "tuniu",
        "count": 1,
        "items": [{"code": "1461"}],
    }
    batch = [
        _event("result_card", "1-0", card),
        _event("assistant_message", "2-0", {"content": "ok"}),
    ]
    client = _ScriptedAgentClient([batch])
    request = _route_request(None, client)

    diagnostics = await conversations._load_run_diagnostics(
        request, _user(), "conv_1", "thread_1", request.state.correlation_context
    )

    stages = diagnostics["stages"]
    assert isinstance(stages, list)
    assert [stage["type"] for stage in stages] == ["result_card"]
    assert stages[0]["data"] == card
