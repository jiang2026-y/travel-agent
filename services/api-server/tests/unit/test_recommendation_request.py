# 文件职责：验证 API 推荐快速操作请求的互斥字段和输入约束。
# 定义 ResumeRequest 快速操作单元测试，确保点击操作不会混入普通恢复消息。
from __future__ import annotations

import pytest
from pydantic import ValidationError

from travel_agent_api.api.routes.conversations import ResumeRequest


def test_quick_action_request_accepts_only_action() -> None:
    """快速操作请求只需要 kind 和 quick_action。"""
    request = ResumeRequest(kind="quick_action", quick_action="确认")
    assert request.quick_action == "确认"


def test_quick_action_request_rejects_message_mix() -> None:
    """快速操作不能同时携带普通消息。"""
    with pytest.raises(ValidationError, match="quick_action_required"):
        ResumeRequest(kind="quick_action", quick_action="确认", message="继续")
