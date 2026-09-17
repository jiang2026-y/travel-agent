# 文件职责：提供用户差旅偏好设置的选项目录、记忆召回解析与保存接口。
# 定义 list_preference_options、get_preferences 与 save_preferences，
# 偏好统一存于百炼长期记忆，本接口不落库、不缓存正文。
from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from travel_agent_api.api.dependencies import (
    get_current_user,
    record_audit_event,
    require_csrf,
)
from travel_agent_api.api.errors import ServiceError
from travel_agent_api.application.auth_service import AuthenticatedUser
from travel_agent_api.config.travel_preference_catalog import (
    catalog_key_options,
    catalog_payload,
    format_preference_sentences,
)
from travel_agent_api.core.correlation import get_correlation_context
from travel_agent_api.infrastructure.agent_client import (
    AgentClient,
    InternalUserContext,
)

router = APIRouter(prefix="/api/v1", tags=["preferences"])


class PreferenceSaveRequest(BaseModel):
    """校验偏好保存请求：key → 取值数组，空对象表示不保存任何项。"""

    model_config = ConfigDict(extra="forbid")
    preferences: dict[str, list[str]] = Field(default_factory=dict, max_length=64)


def _agent_client(request: Request) -> AgentClient:
    """读取 Agent 内部客户端。"""
    return cast(AgentClient, request.app.state.agent_client)


@router.get("/preferences/options")
async def list_preference_options(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    """返回偏好设置页面的分类与选项，内容为服务端唯一定义的目录。"""
    del user
    return {"categories": catalog_payload()}


@router.get("/preferences")
async def get_preferences(
    request: Request, user: AuthenticatedUser = Depends(get_current_user)
) -> dict[str, object]:
    """召回当前用户的长期记忆并解析为结构化偏好，供表单回显。"""
    client = _agent_client(request)
    internal_user = InternalUserContext(user.user_id, user.role, "active")
    correlation = get_correlation_context(request)
    memory = await client.retrieve_memory(
        internal_user, correlation, "出差偏好 舱位 酒店 航班 座位 餐饮 交通"
    )
    summary = str(memory.get("memories") or "")
    available = bool(memory.get("available"))
    preferences: dict[str, list[str]] = {}
    if available and summary.strip():
        parsed = await client.parse_preferences(
            internal_user, correlation, summary, catalog_key_options()
        )
        raw = parsed.get("preferences")
        if isinstance(raw, dict):
            preferences = {
                str(key): [str(item) for item in values]
                for key, values in raw.items()
                if isinstance(values, list) and values
            }
    await record_audit_event(request, "preferences_read", user.user_id, "success")
    return {
        "available": available,
        "memory_summary": summary,
        "preferences": preferences,
        "message": str(memory.get("message") or ""),
    }


@router.post("/preferences", dependencies=[Depends(require_csrf)])
async def save_preferences(
    payload: PreferenceSaveRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    """把勾选格式化为中文偏好句子后写入长期记忆。"""
    sentences = format_preference_sentences(payload.preferences)
    if not sentences:
        raise ServiceError("preference_selection_empty", "请至少选择一项偏好后再保存", False, 422)
    client = _agent_client(request)
    result = await client.record_memory(
        InternalUserContext(user.user_id, user.role, "active"),
        get_correlation_context(request),
        "；".join(sentences),
    )
    saved = bool(result.get("available"))
    await record_audit_event(
        request, "preferences_saved", user.user_id, "success" if saved else "failed"
    )
    return {
        "saved": saved,
        "count": len(sentences),
        "message": str(result.get("message") or ""),
    }
