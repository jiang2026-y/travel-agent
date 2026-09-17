# 文件职责：验证同一会话内开启新一轮时复用 thread_id、不新建会话，并区分越权访问。
# 定义起轮路由的替身请求、会话服务与三个用例：新建会话、续轮复用、越权 404。
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import BackgroundTasks

from travel_agent_api.api.errors import ServiceError
from travel_agent_api.api.routes.conversations import TaskRequest, start_run
from travel_agent_api.application.auth_service import AuthenticatedUser
from travel_agent_api.core.correlation import CorrelationContext
from travel_agent_api.persistence.services import RunSummary


class _ConversationService:
    """记录起轮调用参数的会话服务替身，不访问数据库。"""

    def __init__(self, *, turn_result: RunSummary | None, summary: str = "user: 你好") -> None:
        """保存续轮返回结果与历史摘要。"""
        self.turn_result = turn_result
        self.summary = summary
        self.turns: list[tuple[str, str, str]] = []
        self.starts: list[str] = []

    async def start_run(
        self, user_id: str, message: str, correlation: CorrelationContext
    ) -> RunSummary:
        """新建会话时返回带新 thread 的 Run 摘要。"""
        del correlation
        self.starts.append(message)
        return RunSummary("run_new", "conv_new", "thread_new", "queued")

    async def start_turn(
        self, user_id: str, conversation_id: str, message: str, correlation: CorrelationContext
    ) -> RunSummary | None:
        """续轮时返回复用既有 thread 的 Run 摘要，越权返回 None。"""
        del correlation
        self.turns.append((user_id, conversation_id, message))
        return self.turn_result

    async def get_desensitized_context_summary(
        self, user_id: str, conversation_id: str
    ) -> str:
        """返回固定脱敏历史摘要。"""
        del user_id, conversation_id
        return self.summary


class _AuditService:
    """记录审计事件的替身。"""

    def __init__(self) -> None:
        """初始化空事件列表。"""
        self.events: list[str] = []

    async def record(self, event_type: str, *_: object) -> None:
        """保存事件类型。"""
        self.events.append(event_type)


def _request(service: _ConversationService) -> Any:
    """构造仅暴露路由依赖字段的替身请求。"""
    state = SimpleNamespace(
        correlation_context=CorrelationContext("trace_1", "request_1", "", ""),
        conversation_service=service,
        agent_client=object(),
        audit_service=_AuditService(),
        recommendation_dispatcher=None,
        title_dispatcher=None,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), state=state)


def _user() -> AuthenticatedUser:
    """构造固定普通用户。"""
    return AuthenticatedUser(user_id="user_1", account="traveler", role="user")


@pytest.mark.asyncio
async def test_new_conversation_path_stays_unchanged() -> None:
    """不带 conversation_id 时仍走新建会话分支，并返回 queued 快照。"""
    service = _ConversationService(turn_result=None)

    result = await start_run(
        TaskRequest(message="帮我查航班"), _request(service), BackgroundTasks(), _user()
    )

    assert result["conversation_id"] == "conv_new"
    assert result["status"] == "queued"
    assert service.starts == ["帮我查航班"]
    assert service.turns == []


@pytest.mark.asyncio
async def test_existing_conversation_reuses_thread_id() -> None:
    """带 conversation_id 时在同一会话内开启新一轮，复用既有 thread_id。"""
    service = _ConversationService(
        turn_result=RunSummary("run_turn2", "conv_existing", "thread_existing", "queued")
    )

    result = await start_run(
        TaskRequest(message="就订第一个", conversation_id="conv_existing"),
        _request(service),
        BackgroundTasks(),
        _user(),
    )

    assert result["run_id"] == "run_turn2"
    assert result["conversation_id"] == "conv_existing"
    assert result["thread_id"] == "thread_existing"
    assert service.turns == [("user_1", "conv_existing", "就订第一个")]
    assert service.starts == []


@pytest.mark.asyncio
async def test_foreign_conversation_is_rejected() -> None:
    """会话不存在或不属于当前用户时必须返回 404，而不是静默新建会话。"""
    service = _ConversationService(turn_result=None)

    with pytest.raises(ServiceError) as captured:
        await start_run(
            TaskRequest(message="继续", conversation_id="conv_other"),
            _request(service),
            BackgroundTasks(),
            _user(),
        )

    assert captured.value.code == "conversation_not_found"
    assert captured.value.status_code == 404
    assert service.starts == []
