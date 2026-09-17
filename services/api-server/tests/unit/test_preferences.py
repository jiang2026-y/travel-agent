# 文件职责：验证偏好目录、偏好句子格式化与偏好接口的降级行为。
# 定义目录完整性、句子格式化、读取解析与保存降级测试。
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from travel_agent_api.api.routes.preferences import (
    PreferenceSaveRequest,
    get_preferences,
    list_preference_options,
    save_preferences,
)
from travel_agent_api.application.auth_service import AuthenticatedUser
from travel_agent_api.config.travel_preference_catalog import (
    catalog_key_options,
    catalog_payload,
    format_preference_sentences,
)
from travel_agent_api.core.correlation import CorrelationContext


class _AgentClient:
    """Agent 内部客户端替身，记录记忆读写与解析调用。"""

    def __init__(self, *, available: bool = True, parsed: dict[str, Any] | None = None) -> None:
        """保存可用性与解析结果。"""
        self.available = available
        self.parsed = (
            parsed if parsed is not None else {"preferences": {"flight_cabin": ["公务舱"]}}
        )
        self.recorded: list[str] = []
        self.parse_calls: list[dict[str, Any]] = []

    async def retrieve_memory(self, user: Any, correlation: Any, query: str) -> dict[str, Any]:
        """返回记忆召回结果。"""
        del user, correlation, query
        if not self.available:
            return {"available": False, "memories": "", "message": "长期记忆当前未启用。"}
        return {"available": True, "memories": "用户出差偏好：机票-舱位=公务舱"}

    async def record_memory(self, user: Any, correlation: Any, content: str) -> dict[str, Any]:
        """记录写入内容。"""
        del user, correlation
        self.recorded.append(content)
        return {"available": self.available, "message": "已记录该差旅偏好。"}

    async def parse_preferences(
        self, user: Any, correlation: Any, memory_text: str, catalog: dict[str, list[str]]
    ) -> dict[str, Any]:
        """记录解析入参并返回固定结构。"""
        del user, correlation
        self.parse_calls.append({"memory_text": memory_text, "catalog": catalog})
        return self.parsed


class _AuditService:
    """审计替身，记录事件类型。"""

    def __init__(self) -> None:
        """初始化空事件列表。"""
        self.events: list[str] = []

    async def record(self, event_type: str, *_: object) -> None:
        """保存事件类型。"""
        self.events.append(event_type)


def _request(agent_client: _AgentClient) -> Any:
    """构造仅暴露路由依赖字段的替身请求。"""
    state = SimpleNamespace(
        correlation_context=CorrelationContext("trace", "request", "run", "thread"),
        audit_service=_AuditService(),
        agent_client=agent_client,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), state=state)


def _user() -> AuthenticatedUser:
    """构造普通用户身份。"""
    return AuthenticatedUser(user_id="user_1", account="traveler", role="user")


def test_catalog_covers_java_priority_items() -> None:
    """目录必须覆盖机票/酒店/高铁/出行习惯四类及其关键项。"""
    payload = catalog_payload()
    categories = {category["category"] for category in payload}
    keys = set(catalog_key_options())

    assert categories == {"flight", "hotel", "train", "general"}
    assert {
        "flight_cabin",
        "flight_airline",
        "hotel_brand",
        "hotel_room",
        "train_seat",
        "meal",
    } <= keys
    assert "公务舱" in catalog_key_options()["flight_cabin"]


def test_format_preference_sentences_keeps_only_catalog_values() -> None:
    """只保留目录内的取值，模型或前端传入的非法值被丢弃。"""
    sentences = format_preference_sentences(
        {
            "flight_cabin": ["公务舱", "飞船舱"],
            "hotel_brand": ["全季", "亚朵"],
            "unknown_key": ["x"],
        }
    )

    assert sentences == [
        "用户出差偏好：机票偏好-舱位偏好=公务舱",
        "用户出差偏好：酒店偏好-偏好品牌=全季、亚朵",
    ]


@pytest.mark.asyncio
async def test_get_preferences_parses_memory_when_available() -> None:
    """记忆可用时返回结构化偏好与原文摘要。"""
    client = _AgentClient()
    request = _request(client)

    result = await get_preferences(request, _user())

    assert result["available"] is True
    assert result["preferences"] == {"flight_cabin": ["公务舱"]}
    assert "舱位" in str(result["memory_summary"])
    assert client.parse_calls[0]["catalog"] == catalog_key_options()
    assert request.app.state.audit_service.events == ["preferences_read"]


@pytest.mark.asyncio
async def test_get_preferences_degrades_without_memory() -> None:
    """记忆未启用时不解析、不报错，返回 available=false。"""
    client = _AgentClient(available=False)

    result = await get_preferences(_request(client), _user())

    assert result["available"] is False
    assert result["preferences"] == {}
    assert client.parse_calls == []


@pytest.mark.asyncio
async def test_save_preferences_writes_sentences() -> None:
    """保存时把勾选格式化为偏好句子并写入记忆。"""
    client = _AgentClient()
    request = _request(client)

    result = await save_preferences(
        PreferenceSaveRequest(
            preferences={"flight_cabin": ["公务舱"], "meal": ["素食"]}
        ),
        request,
        _user(),
    )

    assert result["saved"] is True
    assert result["count"] == 2
    assert client.recorded == [
        "用户出差偏好：机票偏好-舱位偏好=公务舱；用户出差偏好：出行习惯-餐饮偏好=素食"
    ]
    assert request.app.state.audit_service.events == ["preferences_saved"]


def test_preference_options_are_served_from_catalog() -> None:
    """选项目录接口返回服务端唯一定义的目录。"""
    import asyncio

    result = asyncio.run(list_preference_options(_user()))

    assert len(result["categories"]) == 4
