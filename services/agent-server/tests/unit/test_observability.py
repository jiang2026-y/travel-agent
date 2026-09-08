# 本文件验证 Agent Server 的关联标识、脱敏审计日志和统一错误信封。
# 定义 test_agent_request_is_correlated_and_redacted，用于验证 HTTP 请求审计。
# 定义 test_agent_service_error_uses_safe_envelope，用于验证业务错误响应。
import json
import logging

import pytest
from fastapi import Body
from fastapi.testclient import TestClient

from travel_agent_agent.api.errors import ServiceError
from travel_agent_agent.main import create_app


def test_agent_request_is_correlated_and_redacted(caplog: pytest.LogCaptureFixture) -> None:
    """Agent 请求必须回传关联标识，并且日志不得包含完整提示词、思维链或密钥明文。"""
    application = create_app()

    @application.post("/_test/audit")
    async def audit_endpoint(payload: dict[str, str] = Body()) -> dict[str, bool]:
        return {"accepted": bool(payload)}

    with caplog.at_level(logging.INFO, logger="travel_agent_agent.audit"):
        response = TestClient(application).post(
            "/_test/audit",
            headers={
                "X-Trace-Id": "trace-agent-001",
                "X-Request-Id": "request-agent-001",
                "X-Run-Id": "run-agent-001",
                "X-Thread-Id": "thread-agent-001",
            },
            json={"prompt": "must-not-be-logged", "chain_of_thought": "private reasoning"},
        )

    assert response.status_code == 200
    assert response.headers["x-trace-id"] == "trace-agent-001"
    assert response.headers["x-request-id"] == "request-agent-001"
    assert response.headers["x-run-id"] == "run-agent-001"
    assert response.headers["x-thread-id"] == "thread-agent-001"
    audit_event = json.loads(caplog.records[-1].message.removeprefix("interface_audit "))
    assert audit_event["trace_id"] == "trace-agent-001"
    assert audit_event["request_id"] == "request-agent-001"
    assert audit_event["run_id"] == "run-agent-001"
    assert audit_event["thread_id"] == "thread-agent-001"
    assert "must-not-be-logged" not in caplog.text
    assert "private reasoning" not in caplog.text


def test_agent_service_error_uses_safe_envelope() -> None:
    """Agent 受控业务异常必须使用统一错误信封，供 API 识别重试语义。"""
    application = create_app()

    @application.get("/_test/error")
    async def error_endpoint() -> None:
        raise ServiceError(
            code="agent_busy", message="智能体暂不可用", retryable=True, status_code=503
        )

    response = TestClient(application).get(
        "/_test/error", headers={"X-Trace-Id": "trace-agent-error"}
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "agent_busy"
    assert response.json()["trace_id"] == "trace-agent-error"
