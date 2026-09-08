# 本文件验证账号密码登录、Cookie 会话、CSRF 和错误信封契约。
# 定义登录、退出、会话查询和敏感信息不回显的 API 测试。

from fastapi.testclient import TestClient

from travel_agent_api.main import create_app


def test_login_sets_http_only_session_and_csrf_cookie() -> None:
    """正确凭据应设置 HttpOnly 会话 Cookie 和可提交的 CSRF Cookie。"""
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/auth/login",
        json={"account": "travel.user", "password": "user-password"},
    )

    assert response.status_code == 200
    assert response.json()["user"] == {
        "user_id": "user_001",
        "account": "travel.user",
        "role": "user",
    }
    assert "password" not in response.text
    cookies = response.cookies
    assert cookies.get("travel_agent_session")
    assert cookies.get("travel_agent_csrf")
    assert "HttpOnly" in response.headers["set-cookie"]


def test_invalid_credentials_use_uniform_error_without_password_echo() -> None:
    """错误账号与错误密码都返回统一错误，且不泄露凭据。"""
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/auth/login",
        json={"account": "unknown", "password": "wrong-secret"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    assert "wrong-secret" not in response.text


def test_invalid_login_payload_uses_safe_error_envelope() -> None:
    """参数错误不能回显密码，且必须使用统一错误信封。"""
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/auth/login",
        json={"account": "travel.user", "password": "hidden-password", "role": "admin"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert "hidden-password" not in response.text


def test_logout_requires_csrf_and_invalidates_session() -> None:
    """退出必须通过 CSRF，成功后原会话不能再访问当前用户接口。"""
    client = TestClient(create_app())
    login = client.post(
        "/api/v1/auth/login",
        json={"account": "travel.user", "password": "user-password"},
    )
    csrf = login.cookies["travel_agent_csrf"]

    denied = client.post("/api/v1/auth/logout")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "csrf_failed"

    ok = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_expired_session_and_login_failure_limit_are_safely_rejected() -> None:
    """过期会话和第六次连续失败均应安全拒绝，且不泄露账号状态。"""
    application = create_app()
    client = TestClient(application)
    login = client.post(
        "/api/v1/auth/login",
        json={"account": "travel.user", "password": "user-password"},
    )
    session_id = login.cookies["travel_agent_session"]
    application.state.auth_service.session_service.store.expires_at[session_id] = 0
    assert client.get("/api/v1/auth/me").status_code == 401

    for _ in range(6):
        response = client.post(
            "/api/v1/auth/login",
            json={"account": "travel.user", "password": "wrong-password"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"
