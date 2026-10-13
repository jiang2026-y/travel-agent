# 文件职责：验证行程管理写工具的扁平入参、ISO 日期校验、幂等返回与落库回查硬失败。
# 定义提交成功、重复申请、参数日期错误、日期范围错误与回查失败测试。
from __future__ import annotations

from contextvars import Token
from typing import Any

import pytest

from travel_agent_agent.agents.itinerary_manage.client import TravelOrderToolError
from travel_agent_agent.agents.itinerary_manage.hitl_context import (
    ToolExecutionAuthorization,
    bind_tool_execution_authorization,
    reset_tool_execution_authorization,
)
from travel_agent_agent.agents.itinerary_manage.schemas import TravelOrderInput
from travel_agent_agent.agents.itinerary_manage.tools import TravelOrderWriteTools


class _RecordingClient:
    """记录内部 API 请求并按路径返回预设结果的最小替身。"""

    def __init__(self, *, duplicate: bool = False, verified: bool = True) -> None:
        self.duplicate = duplicate
        self.verified = verified
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """记录请求并返回提交、回查或修改结果。"""
        self.calls.append((method, path, kwargs))
        if method == "POST" and path == "/internal/v1/travel-orders":
            return {
                "order": {
                    "order_id": "order_1",
                    "approval_id": "approval_1",
                    "status": "SUBMITTED",
                },
                "status": "submitted",
                "duplicate": self.duplicate,
            }
        if method == "GET" and path == "/internal/v1/travel-orders":
            if not self.verified:
                return {"orders": []}
            return {"orders": [{"order_id": "order_1", "status": "SUBMITTED"}]}
        return {
            "order": {"order_id": "order_1", "status": "SUBMITTED"},
            "status": "resubmitted",
        }


def _bind_authorization() -> Token[ToolExecutionAuthorization | None]:
    """绑定一次确认授权，返回用于清理的上下文令牌。"""
    return bind_tool_execution_authorization(ToolExecutionAuthorization("hitl_1", "token_1"))


@pytest.mark.asyncio
async def test_submit_sends_flat_iso_body_and_reports_creation() -> None:
    """提交必须只传五个出行要素原值，并如实标记为新建。"""
    client = _RecordingClient()
    tools = TravelOrderWriteTools(client)
    token = _bind_authorization()
    try:
        record = await tools.submit_travel_approval(
            "杭州", "北京", "2026-09-18", "2026-09-20", "客户拜访"
        )
    finally:
        reset_tool_execution_authorization(token)

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("POST", "/internal/v1/travel-orders")
    assert kwargs["body"] == {
        "destination": "杭州",
        "departure_city": "北京",
        "departure_date": "2026-09-18",
        "return_date": "2026-09-20",
        "purpose": "客户拜访",
    }
    assert kwargs["confirmation_token"] == "token_1"
    assert kwargs["interaction_id"] == "hitl_1"
    assert kwargs["idempotency_key"] == "hitl_1"
    assert client.calls[1][2]["params"] == {"order_id": "order_1"}
    assert record.success is True
    assert record.idempotent is False
    assert record.order_id == "order_1"
    assert "待审批" in record.message


@pytest.mark.asyncio
async def test_submit_reports_duplicate_existing_order() -> None:
    """服务端返回重复标记时必须如实说明已存在、未重复创建。"""
    client = _RecordingClient(duplicate=True)
    tools = TravelOrderWriteTools(client)
    token = _bind_authorization()
    try:
        record = await tools.submit_travel_approval(
            "杭州", "北京", "2026-09-18", "2026-09-20", "客户拜访"
        )
    finally:
        reset_tool_execution_authorization(token)

    assert record.idempotent is True
    assert "已存在" in record.message
    assert "order_1" in record.message


@pytest.mark.asyncio
async def test_submit_rejects_relative_date_before_any_request() -> None:
    """相对日期必须在调用后端前被拒绝，保证确认指纹与写库参数一致。"""
    client = _RecordingClient()
    tools = TravelOrderWriteTools(client)

    with pytest.raises(TravelOrderToolError) as error:
        await tools.submit_travel_approval(
            "杭州", "北京", "明天", "2026-09-20", "客户拜访"
        )

    assert error.value.code == "INVALID_DATE"
    assert "YYYY-MM-DD" in error.value.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_submit_rejects_return_date_before_departure() -> None:
    """返程早于出发时必须返回稳定参数错误。"""
    tools = TravelOrderWriteTools(_RecordingClient())

    with pytest.raises(TravelOrderToolError) as error:
        await tools.submit_travel_approval(
            "杭州", "北京", "2026-09-20", "2026-09-18", "客户拜访"
        )

    assert error.value.code == "INVALID_DATE_RANGE"


@pytest.mark.asyncio
async def test_submit_fails_hard_when_verification_misses() -> None:
    """落库回查查不到或状态不符时必须硬失败，不得返回软标记。"""
    client = _RecordingClient(verified=False)
    tools = TravelOrderWriteTools(client)
    token = _bind_authorization()
    try:
        with pytest.raises(TravelOrderToolError) as error:
            await tools.submit_travel_approval(
                "杭州", "北京", "2026-09-18", "2026-09-20", "客户拜访"
            )
    finally:
        reset_tool_execution_authorization(token)

    assert error.value.code == "VERIFY_FAILED"


@pytest.mark.asyncio
async def test_modify_rejects_relative_date_before_any_request() -> None:
    """修改工具的日期同样只接受 YYYY-MM-DD。"""
    client = _RecordingClient()
    tools = TravelOrderWriteTools(client)

    with pytest.raises(TravelOrderToolError) as error:
        await tools.modify_travel_order(
            "order_1", TravelOrderInput(departure_date="明天", purpose="客户拜访")
        )

    assert error.value.code == "INVALID_DATE"
    assert client.calls == []
