# 本文件验证 Agent Server 内部命令的 Docker Secret 认证和二次用户上下文校验。
# 定义内部命令认证缺失、身份头不一致、隐私状态限制与成功停止记忆的测试函数。
from fastapi.testclient import TestClient

from travel_agent_agent.core.settings import Settings
from travel_agent_agent.main import create_app


def _create_client(tmp_path) -> tuple[TestClient, str]:
    """创建使用临时 Docker Secret 文件的 Agent 测试客户端。"""
    token = "a" * 32
    token_file = tmp_path / "api_agent_internal_token"
    token_file.write_text(token, encoding="utf-8")
    settings = Settings.from_environment(
        {
            "TRAVEL_AGENT_ENV": "development",
            "TRAVEL_AGENT_EXTERNAL_MODE": "real_readonly",
            "API_AGENT_INTERNAL_TOKEN_FILE": str(token_file),
        }
    )
    return TestClient(create_app(settings)), token


def _command_payload(privacy_status: str = "deletion_pending") -> dict[str, object]:
    """返回满足基础命令格式的内部停止记忆请求体。"""
    return {
        "command_version": "v1",
        "command": "suspend_user_memory",
        "request_id": "request_001",
        "trace_id": "trace_001",
        "user": {"user_id": "user_001", "role": "user", "privacy_status": privacy_status},
        "reason": "account_deletion_requested",
    }


def _identity_headers(token: str, privacy_status: str = "deletion_pending") -> dict[str, str]:
    """返回通过二次校验所需的认证和用户上下文请求头。"""
    return {
        "Authorization": f"Bearer {token}",
        "X-Request-Id": "request_001",
        "X-Trace-Id": "trace_001",
        "X-Internal-User-Id": "user_001",
        "X-Internal-User-Role": "user",
        "X-Internal-Privacy-Status": privacy_status,
    }


def test_internal_command_rejects_missing_service_token(tmp_path) -> None:
    """缺少 Docker Secret Bearer Token 的请求不得触发任何内部命令。"""
    client, _ = _create_client(tmp_path)

    response = client.post("/internal/v1/commands/suspend-user-memory", json=_command_payload())

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "internal_service_auth_failed"


def test_internal_command_rejects_mismatched_user_context(tmp_path) -> None:
    """身份头与命令正文的用户、角色或隐私状态不一致时必须拒绝。"""
    client, token = _create_client(tmp_path)
    headers = _identity_headers(token)
    headers["X-Internal-User-Id"] = "user_002"

    response = client.post(
        "/internal/v1/commands/suspend-user-memory",
        headers=headers,
        json=_command_payload(),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "internal_user_context_mismatch"


def test_internal_command_rejects_active_privacy_state(tmp_path) -> None:
    """仅有活动状态的用户不允许通过停止记忆命令绕过正常会话流程。"""
    client, token = _create_client(tmp_path)

    response = client.post(
        "/internal/v1/commands/suspend-user-memory",
        headers=_identity_headers(token, "active"),
        json=_command_payload("active"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "privacy_state_not_suspendable"


def test_internal_command_requires_matching_command_correlation(tmp_path) -> None:
    """停止记忆命令必须显式包含并匹配 request_id 与 trace_id，不能省略调用链身份。"""
    client, token = _create_client(tmp_path)
    payload = _command_payload()
    payload.pop("request_id")

    response = client.post(
        "/internal/v1/commands/suspend-user-memory",
        headers=_identity_headers(token),
        json=payload,
    )

    assert response.status_code == 422


def test_internal_command_accepts_deletion_pending_user(tmp_path) -> None:
    """删除已受理的用户在认证和二次校验通过后应获得停止记忆确认。"""
    client, token = _create_client(tmp_path)

    response = client.post(
        "/internal/v1/commands/suspend-user-memory",
        headers=_identity_headers(token),
        json=_command_payload(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "command": "suspend_user_memory",
        "status": "memory_suspended",
        "user_id": "user_001",
        "privacy_status": "deletion_pending",
    }
