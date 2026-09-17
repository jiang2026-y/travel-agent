# 文件职责：提供经过认证的旅行 Agent 能力清单；定义 agent_capabilities 只读接口。
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request

from travel_agent_api.api.dependencies import get_current_user
from travel_agent_api.application.auth_service import AuthenticatedUser

router = APIRouter(prefix="/api/v1", tags=["capabilities"])


@router.get("/agent-capabilities")
async def get_agent_capabilities(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    """返回不包含提示词、凭据或模型原始消息的 Agent 能力说明。"""
    del user
    tuniu_configured = os.environ.get("TUNIU_PROVIDER_CONFIGURED", "false").strip().lower() in {
        "1", "true"
    }
    booking_enabled = (
        request.app.state.settings.external_mode == "real_tuniu"
        and request.app.state.settings.tuniu_approved
        and tuniu_configured
    )
    info_enabled = all(
        os.environ.get(name, "false").strip().lower() in {"1", "true"}
        for name in ("BAILIAN_ENABLED", "BAILIAN_CONFIGURED")
    )
    return {
        "agents": [
            {
                "name": "masterAgent",
                "display_name": "主智能体",
                "status": "enabled",
                "intents": ["reimbursement", "greeting", "unknown"],
                "tool_categories": ["只读信息查询", "澄清交互", "子智能体调度"],
                "supports_hitl": True,
                "supports_write": False,
            },
            {
                "name": "itineraryManageAgent",
                "display_name": "行程管理智能体",
                "status": "enabled",
                "intents": [
                    "travel_application",
                    "travel_cancel",
                    "travel_modify",
                    "approval_query",
                    "travel_order_query",
                ],
                "tool_categories": ["差旅单查询", "审批查询", "差旅单写入（需审批）", "用户信息"],
                "supports_hitl": True,
                "supports_write": True,
            },
            {
                "name": "itineraryPlanAgent",
                "display_name": "行程规划智能体",
                "status": "registered_disabled",
                "intents": [],
                "tool_categories": [],
                "supports_hitl": False,
                "supports_write": False,
            },
            {
                "name": "bookingAgent",
                "display_name": "预订与查询智能体",
                "status": "enabled" if booking_enabled else "readonly",
                "intents": ["booking", "flight_search", "train_search", "hotel_search"],
                "tool_categories": [
                    "航班/酒店/火车查询",
                    "预订记录查询",
                    "确认后下单",
                    "用户信息查询",
                ],
                "supports_hitl": booking_enabled,
                "supports_write": booking_enabled,
            },
            {
                "name": "infoAgent",
                "display_name": "信息查询智能体",
                "status": "enabled" if info_enabled else "registered_disabled",
                "intents": ["policy_query", "attractions_query", "general_info"],
                "tool_categories": ["信息查询"],
                "supports_hitl": False,
                "supports_write": False,
            },
            {
                "name": "itineraryReviewAgent",
                "display_name": "行程审核智能体",
                "status": "registered_disabled",
                "intents": [],
                "tool_categories": [],
                "supports_hitl": False,
                "supports_write": False,
            },
        ]
    }
