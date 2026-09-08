# 本文件验证 Tool Gateway 的关联标识和脱敏审计日志。
# 定义 test_gateway_health_request_is_correlated_and_redacted。
# 该测试用于验证 Gateway HTTP 边界的安全日志。
import json
import logging

import pytest
from fastapi.testclient import TestClient

from travel_agent_tool_gateway.main import create_app


def test_gateway_health_request_is_correlated_and_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Gateway 健康请求必须带关联标识，且 Authorization 明文不得进入审计日志。"""
    with caplog.at_level(logging.INFO, logger="travel_agent_tool_gateway.audit"):
        response = TestClient(create_app()).get(
            "/health",
            headers={
                "X-Trace-Id": "trace-gateway-001",
                "X-Request-Id": "request-gateway-001",
                "X-Run-Id": "run-gateway-001",
                "X-Thread-Id": "thread-gateway-001",
                "Authorization": "Bearer must-not-be-logged",
            },
        )

    assert response.status_code == 200
    assert response.headers["x-trace-id"] == "trace-gateway-001"
    assert response.headers["x-request-id"] == "request-gateway-001"
    assert response.headers["x-run-id"] == "run-gateway-001"
    assert response.headers["x-thread-id"] == "thread-gateway-001"
    audit_event = json.loads(caplog.records[-1].message.removeprefix("interface_audit "))
    assert audit_event["trace_id"] == "trace-gateway-001"
    assert audit_event["request_id"] == "request-gateway-001"
    assert audit_event["run_id"] == "run-gateway-001"
    assert audit_event["thread_id"] == "thread-gateway-001"
    assert "must-not-be-logged" not in caplog.text
