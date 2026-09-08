# 本文件提供 API Server 与 Agent Server 共享测试夹具。
# 定义普通用户、管理员、Cookie/CSRF、关联标识和外部能力拒绝策略夹具。
from __future__ import annotations

import pytest

from travel_agent_api.infrastructure.test_doubles import PreconfiguredUser


@pytest.fixture
def ordinary_user() -> PreconfiguredUser:
    """返回预置普通用户，不包含密码或其他真实凭据。"""
    return PreconfiguredUser(user_id="user_001", account="travel.user", role="user")


@pytest.fixture
def administrator() -> PreconfiguredUser:
    """返回预置管理员用户，不包含密码或其他真实凭据。"""
    return PreconfiguredUser(user_id="admin_001", account="travel.admin", role="admin")


@pytest.fixture
def csrf_headers() -> dict[str, str]:
    """返回测试专用 CSRF 请求头。"""
    return {"X-CSRF-Token": "test-csrf-token"}


@pytest.fixture
def correlation_headers() -> dict[str, str]:
    """返回四类固定关联标识请求头。"""
    return {
        "X-Trace-Id": "trace_test_001",
        "X-Request-Id": "request_test_001",
        "X-Run-Id": "run_test_001",
        "X-Thread-Id": "thread_test_001",
    }


@pytest.fixture
def agent_user_context() -> dict[str, str]:
    """返回 Agent 命令使用的预置普通用户上下文。"""
    return {"user_id": "user_001", "role": "user", "privacy_status": "active"}


@pytest.fixture
def agent_correlation_headers() -> dict[str, str]:
    """返回 Agent HTTP 边界使用的四类关联标识。"""
    return {
        "X-Trace-Id": "trace_test_001",
        "X-Request-Id": "request_test_001",
        "X-Run-Id": "run_test_001",
        "X-Thread-Id": "thread_test_001",
    }


@pytest.fixture
def external_capability_policy() -> str:
    """声明测试默认外部能力拒绝策略。"""
    return "not_configured_policy_denied"
