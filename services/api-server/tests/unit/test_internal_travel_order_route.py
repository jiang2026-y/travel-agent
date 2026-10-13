# 文件职责：验证内部差旅申请提交接口的确认凭证消费、扁平参数指纹与幂等响应。
# 定义内部 Token 校验、缺少确认凭证、单号不接受调用方指定与重复提交响应测试。
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from travel_agent_api.application.travel_order_service import SubmitOutcome
from travel_agent_api.core.settings import Settings
from travel_agent_api.main import create_app
from travel_agent_api.persistence.models import TravelOrder

_TOKEN = "t" * 32
_BODY = {
    "destination": "杭州",
    "departure_city": "北京",
    "departure_date": "2026-09-18",
    "return_date": "2026-09-20",
    "purpose": "客户拜访",
}


class _RecordingAgentClient:
    """记录确认凭证消费参数的最小 Agent 客户端替身。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def consume_hitl_token(
        self,
        user: Any,
        correlation: Any,
        conversation_id: str,
        interaction_id: str,
        confirmation_token: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> str:
        """记录一次确认消费并返回交互标识作为幂等键。"""
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "interaction_id": interaction_id,
                "confirmation_token": confirmation_token,
                "tool_name": tool_name,
                "tool_args": tool_args,
            }
        )
        return interaction_id


class _FakeTravelOrderService:
    """记录提交入参并返回预定幂等结果的差旅服务替身。"""

    def __init__(self, duplicate: bool) -> None:
        self.duplicate = duplicate
        self.calls: list[dict[str, Any]] = []

    async def submit(
        self, *, user_id: str, payload: dict[str, Any], idempotency_key: str
    ) -> SubmitOutcome:
        """按入参生成差旅单并返回是否命中要素级幂等。"""
        self.calls.append(
            {"user_id": user_id, "payload": payload, "idempotency_key": idempotency_key}
        )
        order = TravelOrder(
            order_id=idempotency_key,
            user_id=user_id,
            destination=payload.get("destination"),
            departure_city=payload.get("departure_city"),
            departure_date=payload.get("departure_date"),
            return_date=payload.get("return_date"),
            purpose=payload.get("purpose"),
            status="SUBMITTED",
            approval_id="approval_1",
        )
        return SubmitOutcome(order, duplicate=self.duplicate)


def _client(
    tmp_path: Any, duplicate: bool = False
) -> tuple[TestClient, _FakeTravelOrderService, _RecordingAgentClient]:
    """构造带内部 Token 与替身服务的 API 应用。"""
    token_file = tmp_path / "api_agent_internal_token"
    token_file.write_text(_TOKEN, encoding="utf-8")
    settings = Settings.from_environment(
        {
            "TRAVEL_AGENT_ENV": "test",
            "API_AGENT_INTERNAL_TOKEN_FILE": str(token_file),
        }
    )
    application = create_app(settings)
    service = _FakeTravelOrderService(duplicate)
    agent_client = _RecordingAgentClient()
    application.state.travel_order_service = service
    application.state.agent_client = agent_client
    return TestClient(application), service, agent_client


def _headers(*, confirmation: bool = True, idempotency_key: str = "hitl_1") -> dict[str, str]:
    """返回内部 Token、用户上下文、确认凭证与关联标识请求头。"""
    headers = {
        "Authorization": f"Bearer {_TOKEN}",
        "X-Internal-User-Id": "user_1",
        "X-Internal-User-Role": "user",
        "X-Conversation-Id": "conv_1",
        "X-HITL-Interaction-Id": "hitl_1",
        "X-Idempotency-Key": idempotency_key,
        "X-Trace-Id": "trace_1",
        "X-Request-Id": "request_1",
        "X-Run-Id": "run_1",
        "X-Thread-Id": "thread_1",
    }
    if confirmation:
        headers["X-Confirmation-Token"] = "confirmation-token-value"
    return headers


def test_submit_route_consumes_token_with_flat_iso_arguments(tmp_path: Any) -> None:
    """确认指纹必须是五个扁平参数且日期为 ISO 字符串，服务端自行生成单号。"""
    client, service, agent_client = _client(tmp_path)

    response = client.post("/internal/v1/travel-orders", headers=_headers(), json=_BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "submitted"
    assert body["duplicate"] is False
    assert body["order"]["order_id"].startswith("order_")
    assert agent_client.calls[0]["tool_name"] == "submit_travel_approval"
    assert agent_client.calls[0]["tool_args"] == _BODY
    assert service.calls[0]["payload"]["departure_date"].isoformat() == "2026-09-18"
    assert service.calls[0]["payload"]["return_date"].isoformat() == "2026-09-20"
    assert service.calls[0]["payload"].get("order_id") is None


def test_submit_route_reports_duplicate_existing_order(tmp_path: Any) -> None:
    """命中要素级幂等时响应 duplicate 为真，且仍返回已有差旅单。"""
    client, _, _ = _client(tmp_path, duplicate=True)

    response = client.post("/internal/v1/travel-orders", headers=_headers(), json=_BODY)

    assert response.status_code == 200
    assert response.json()["duplicate"] is True


def test_submit_route_requires_confirmation_token(tmp_path: Any) -> None:
    """缺少确认凭证时必须拒绝，且不得消费任何交互。"""
    client, service, agent_client = _client(tmp_path)

    response = client.post(
        "/internal/v1/travel-orders", headers=_headers(confirmation=False), json=_BODY
    )

    assert response.status_code == 409
    assert agent_client.calls == []
    assert service.calls == []


def test_submit_route_rejects_caller_supplied_order_id(tmp_path: Any) -> None:
    """调用方不得指定差旅单号，多余字段一律校验失败。"""
    client, service, _ = _client(tmp_path)

    response = client.post(
        "/internal/v1/travel-orders",
        headers=_headers(),
        json={**_BODY, "order_id": "order_supplied"},
    )

    assert response.status_code == 422
    assert service.calls == []


def test_submit_route_rejects_mismatched_idempotency_key(tmp_path: Any) -> None:
    """请求头幂等键与已消费交互标识不一致时必须拒绝写入。"""
    client, service, _ = _client(tmp_path)

    response = client.post(
        "/internal/v1/travel-orders",
        headers=_headers(idempotency_key="hitl_other"),
        json=_BODY,
    )

    assert response.status_code == 403
    assert service.calls == []
