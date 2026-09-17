# 文件职责：验证会话标题后台任务生成、落库与失败隔离。
# 定义标题落库、空标题跳过、异常只记录 warning 与意图 JSON 构造测试。
from __future__ import annotations

from typing import Any

import pytest

from travel_agent_api.api.routes.conversations import _intent_json
from travel_agent_api.application.title_service import ConversationTitleDispatcher
from travel_agent_api.core.correlation import CorrelationContext
from travel_agent_api.infrastructure.agent_client import InternalUserContext


class _FakeAgentClient:
    """返回固定标题或抛出异常的 Agent 客户端替身。"""

    def __init__(self, title: str | None = "上海出差申请", error: Exception | None = None) -> None:
        self.title = title
        self.error = error
        self.calls = 0

    async def generate_conversation_title(self, *args: Any, **kwargs: Any) -> str | None:
        """记录调用并返回预设标题。"""
        del args, kwargs
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.title


class _FakeConversationService:
    """记录标题更新调用的会话服务替身。"""

    def __init__(self) -> None:
        self.updates: list[tuple[str, str, str]] = []

    async def update_title(self, user_id: str, conversation_id: str, title: str) -> str | None:
        """记录标题更新并返回新标题。"""
        self.updates.append((user_id, conversation_id, title))
        return title


def _correlation() -> CorrelationContext:
    """构造固定关联上下文。"""
    return CorrelationContext(
        trace_id="trace", request_id="request", run_id="run", thread_id="thread"
    )


@pytest.mark.asyncio
async def test_title_is_persisted_after_generation() -> None:
    """生成成功后必须把标题写回会话，且不改动 Run 结果。"""
    agent_client = _FakeAgentClient()
    service = _FakeConversationService()
    dispatcher = ConversationTitleDispatcher(agent_client)  # type: ignore[arg-type]
    await dispatcher.dispatch(
        InternalUserContext("user_1", "user", "active"),
        _correlation(),
        service,  # type: ignore[arg-type]
        "conv_1",
        "thread",
        "下周三去上海出差",
        '{"primary_intent": "travel_application"}',
    )
    assert agent_client.calls == 1
    assert service.updates == [("user_1", "conv_1", "上海出差申请")]


@pytest.mark.asyncio
async def test_empty_title_and_failures_do_not_touch_conversation() -> None:
    """空标题或模型失败时不得写库，也不得抛出异常影响主流程。"""
    service = _FakeConversationService()
    empty = ConversationTitleDispatcher(_FakeAgentClient(title=None))  # type: ignore[arg-type]
    failing = ConversationTitleDispatcher(
        _FakeAgentClient(error=RuntimeError("boom"))  # type: ignore[arg-type]
    )
    for dispatcher in (empty, failing):
        await dispatcher.dispatch(
            InternalUserContext("user_1", "user", "active"),
            _correlation(),
            service,  # type: ignore[arg-type]
            "conv_1",
            "thread",
            "下周三去上海出差",
            "",
        )
    assert service.updates == []


def test_intent_json_contains_only_intent_and_source() -> None:
    """标题生成入参只包含意图码与来源，不携带用户正文。"""
    agent_run = type(
        "Run", (), {"intent_code": "travel_application", "intent_source": "VECTOR"}
    )()
    payload = _intent_json(agent_run)
    assert payload == '{"primary_intent": "travel_application", "intent_source": "VECTOR"}'
