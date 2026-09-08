# 本文件验证 API Server 的关联标识、脱敏审计日志和统一错误信封。
# 定义 test_api_request_is_correlated_and_redacted，用于验证 HTTP 请求审计。
# 定义 test_api_service_error_uses_safe_envelope，用于验证业务错误响应。
import json
import logging

import pytest
from fastapi import Body
from fastapi.testclient import TestClient

from travel_agent_api.api.errors import ServiceError
from travel_agent_api.main import create_app


def test_api_request_is_correlated_and_redacted(caplog: pytest.LogCaptureFixture) -> None:
    """API 请求必须回传关联标识，并且日志不得包含密码、验证码或令牌明文。"""
    application = create_app()

    @application.post("/_test/audit")
    async def audit_endpoint(payload: dict[str, str] = Body()) -> dict[str, bool]:
        return {"accepted": bool(payload)}

    with caplog.at_level(logging.INFO, logger="travel_agent_api.audit"):
        response = TestClient(application).post(
            "/_test/audit",
            headers={
                "X-Trace-Id": "trace-api-001",
                "X-Request-Id": "request-api-001",
                "X-Run-Id": "run-api-001",
                "X-Thread-Id": "thread-api-001",
                "Authorization": "Bearer must-not-be-logged",
            },
            json={"password": "must-not-be-logged", "sms_code": "123456", "city": "Shanghai"},
        )

    assert response.status_code == 200
    assert response.headers["x-trace-id"] == "trace-api-001"
    assert response.headers["x-request-id"] == "request-api-001"
    assert response.headers["x-run-id"] == "run-api-001"
    assert response.headers["x-thread-id"] == "thread-api-001"
    audit_event = json.loads(caplog.records[-1].message.removeprefix("interface_audit "))
    assert audit_event["trace_id"] == "trace-api-001"
    assert audit_event["request_id"] == "request-api-001"
    assert audit_event["run_id"] == "run-api-001"
    assert audit_event["thread_id"] == "thread-api-001"
    assert audit_event["request"]["content_length"] > 0
    assert "must-not-be-logged" not in caplog.text
    assert "123456" not in caplog.text


def test_api_service_error_uses_safe_envelope() -> None:
    """受控业务异常必须返回可关联、可重试语义明确且不泄露内部细节的错误信封。"""
    application = create_app()

    @application.get("/_test/error")
    async def error_endpoint() -> None:
        raise ServiceError(
            code="upstream_unavailable", message="上游服务暂不可用", retryable=True, status_code=503
        )

    response = TestClient(application).get(
        "/_test/error", headers={"X-Trace-Id": "trace-api-error"}
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "upstream_unavailable",
        "message": "上游服务暂不可用",
        "retryable": True,
    }
    assert response.json()["trace_id"] == "trace-api-error"
    assert response.headers["x-trace-id"] == "trace-api-error"
