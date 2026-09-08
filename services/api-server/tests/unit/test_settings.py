# 本文件验证 API Server 的环境、安全和真实只读 Provider 配置。
# 定义开发默认值、生产 Cookie 约束、CORS 白名单和 Provider 拒绝规则测试。
import pytest
from fastapi.testclient import TestClient

from travel_agent_api.core.settings import ProviderConfigurationError, Settings
from travel_agent_api.main import create_app


def test_development_defaults_enable_csrf_and_disable_secure_cookie() -> None:
    """开发环境必须启用 CSRF，并允许非 HTTPS 的本地 Cookie 用于本地联调。"""
    settings = Settings.from_environment(
        {"TRAVEL_AGENT_ENV": "development", "TRAVEL_AGENT_EXTERNAL_MODE": "real_readonly"}
    )

    assert settings.session_cookie_name == "travel_agent_session"
    assert settings.session_cookie_secure is False
    assert settings.session_cookie_samesite == "lax"
    assert settings.csrf_enabled is True
    assert settings.csrf_header_name == "X-CSRF-Token"
    assert settings.cors_allowed_origins == ("http://localhost:5173",)
    assert settings.agent_server_base_url == "http://agent-server:8001"
    assert settings.api_agent_token_file == "/run/secrets/api_agent_internal_token"


def test_production_rejects_insecure_session_cookie() -> None:
    """生产环境不得允许非 Secure 会话 Cookie，防止会话在明文链路泄露。"""
    with pytest.raises(ValueError, match="production_cookie_must_be_secure"):
        Settings.from_environment(
            {
                "TRAVEL_AGENT_ENV": "production",
                "TRAVEL_AGENT_EXTERNAL_MODE": "real_readonly",
                "TRAVEL_AGENT_COOKIE_SECURE": "false",
            }
        )


def test_unregistered_or_incomplete_provider_is_denied() -> None:
    """没有完整 Provider 登记时，真实只读调用必须被拒绝而非回退到 Fake。"""
    missing_settings = Settings.from_environment(
        {"TRAVEL_AGENT_ENV": "development", "TRAVEL_AGENT_EXTERNAL_MODE": "real_readonly"}
    )
    incomplete_settings = Settings.from_environment(
        {
            "TRAVEL_AGENT_ENV": "development",
            "TRAVEL_AGENT_EXTERNAL_MODE": "real_readonly",
            "TRAVEL_AGENT_PROVIDER_KEY": "travel",
        }
    )

    with pytest.raises(ProviderConfigurationError, match="provider_not_configured"):
        missing_settings.require_readonly_provider()
    with pytest.raises(ProviderConfigurationError, match="provider_configuration_incomplete"):
        incomplete_settings.require_readonly_provider()


def test_cors_allows_only_configured_frontend_origin() -> None:
    """浏览器跨域请求仅可来自已配置前端域名，且必须支持 CSRF 请求头。"""
    application = create_app(
        Settings.from_environment(
            {
                "TRAVEL_AGENT_ENV": "development",
                "TRAVEL_AGENT_EXTERNAL_MODE": "real_readonly",
                "TRAVEL_AGENT_CORS_ALLOWED_ORIGINS": "http://localhost:5173",
            }
        )
    )

    response = TestClient(application).options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-CSRF-Token",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "X-CSRF-Token" in response.headers["access-control-allow-headers"]
