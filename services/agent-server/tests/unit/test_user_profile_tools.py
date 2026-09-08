# 本文件验证档案工具仅接收完整度结果并刷新非敏感 SessionCtx。
# 定义伪客户端、查询完整度和更新常驻城市测试。
from __future__ import annotations

import pytest

from travel_agent_agent.agents.common.session_context import (
    SessionCtx,
    bind_session_context,
    get_session_context,
    reset_session_context,
)
from travel_agent_agent.agents.itinerary_manage.schemas import UserBaseLocationUpdateRequest
from travel_agent_agent.agents.itinerary_manage.tools import QueryUserInfoTools


class _ProfileClient:
    """提供不含敏感明文的档案接口替身。"""

    async def request(self, method: str, path: str, **_: object) -> dict[str, object]:
        """按请求路径返回完整度或城市更新结果。"""
        if path.endswith("/contact") and method == "GET":
            return {
                "flightComplete": False,
                "hotelComplete": True,
                "trainComplete": False,
                "missingFields": {"flight": ["证件号"], "hotel": [], "train": ["证件号"]},
                "message": "机票预订还缺：证件号",
            }
        return {
            "success": True,
            "baseCity": "上海",
            "updatedFields": ["base_city"],
            "message": "常驻城市已更新",
        }


@pytest.mark.asyncio
async def test_profile_tool_updates_non_sensitive_session_state() -> None:
    """查询完整度只更新 SessionCtx 状态，不保留用户档案明文。"""
    token = bind_session_context(SessionCtx(user_id="user_001"))
    try:
        result = await QueryUserInfoTools(_ProfileClient()).query_user_contact_info()  # type: ignore[arg-type]
        context = get_session_context()
        assert result.flight_complete is False
        assert context.flight_complete is False
        assert context.hotel_complete is True
        assert not hasattr(context, "phone")
    finally:
        reset_session_context(token)


@pytest.mark.asyncio
async def test_base_city_update_refreshes_session_context() -> None:
    """更新常驻城市后当前请求上下文应使用新城市。"""
    token = bind_session_context(SessionCtx(user_id="user_001"))
    try:
        await QueryUserInfoTools(_ProfileClient()).update_user_base_location(  # type: ignore[arg-type]
            UserBaseLocationUpdateRequest(base_city="上海")
        )
        assert get_session_context().base_city == "上海"
    finally:
        reset_session_context(token)
