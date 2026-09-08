# 本文件定义 API Server 的环境与安全配置。
# 定义 ProviderConfigurationError，用于拒绝不完整 Provider。
# 定义 ProviderRegistration，用于校验 Provider 登记；定义 Settings，用于读取安全配置。
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit


class ProviderConfigurationError(RuntimeError):
    """表示真实只读 Provider 因未登记、信息不完整或未审批而被拒绝。"""


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    """保存不包含明文密钥的 Provider 版本、地址和密钥引用。"""

    provider_key: str
    version: str
    base_url: str
    secret_ref: str

    @property
    def has_any_value(self) -> bool:
        """判断是否开始登记 Provider，避免将部分配置静默视为未配置。"""
        return any((self.provider_key, self.version, self.base_url, self.secret_ref))

    @property
    def is_complete(self) -> bool:
        """判断 Provider 是否具备最小只读调用登记信息。"""
        return all((self.provider_key, self.version, self.base_url, self.secret_ref))

    def require_complete(self) -> None:
        """拒绝未登记、部分登记或非 HTTPS 的真实 Provider。"""
        if not self.has_any_value:
            raise ProviderConfigurationError("provider_not_configured")
        if not self.is_complete:
            raise ProviderConfigurationError("provider_configuration_incomplete")
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ProviderConfigurationError("provider_base_url_must_use_https")


@dataclass(frozen=True, slots=True)
class Settings:
    """保存 API Server 的运行环境、会话、CSRF、跨域和 Provider 安全开关。"""

    environment: str
    external_mode: str
    session_cookie_name: str
    session_cookie_secure: bool
    session_cookie_samesite: str
    csrf_cookie_name: str
    csrf_header_name: str
    csrf_enabled: bool
    privacy_features_enabled: bool
    session_ttl_seconds: int
    login_failure_limit: int
    login_failure_window_seconds: int
    database_url: str
    database_migration_url: str
    data_encryption_key_file: str
    persistence_enabled: bool
    cors_allowed_origins: tuple[str, ...]
    agent_server_base_url: str
    api_agent_token_file: str
    provider_registration: ProviderRegistration
    provider_approved: bool

    @property
    def provider_call_enabled(self) -> bool:
        """仅在完整且已审批时标记为可申请调用；Tool Gateway 仍会二次拒绝。"""
        return self.provider_registration.is_complete and self.provider_approved

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """从环境变量读取配置，并对不安全或歧义值立即失败。"""
        values = environ or os.environ
        environment = values.get("TRAVEL_AGENT_ENV", "development")
        external_mode = values.get("TRAVEL_AGENT_EXTERNAL_MODE", "real_readonly")
        if external_mode != "real_readonly":
            raise ValueError("external_mode_must_be_real_readonly")
        session_cookie_secure = _read_bool(
            values, "TRAVEL_AGENT_COOKIE_SECURE", default=environment == "production"
        )
        session_cookie_samesite = values.get("TRAVEL_AGENT_COOKIE_SAMESITE", "lax").lower()
        if session_cookie_samesite not in {"lax", "strict", "none"}:
            raise ValueError("cookie_samesite_invalid")
        if environment == "production" and not session_cookie_secure:
            raise ValueError("production_cookie_must_be_secure")
        if session_cookie_samesite == "none" and not session_cookie_secure:
            raise ValueError("samesite_none_cookie_must_be_secure")
        origins = _read_origins(
            values.get("TRAVEL_AGENT_CORS_ALLOWED_ORIGINS", "http://localhost:5173")
        )
        agent_server_base_url = values.get("AGENT_SERVER_BASE_URL", "http://agent-server:8001")
        parsed_agent_url = urlsplit(agent_server_base_url)
        if parsed_agent_url.scheme != "http" or parsed_agent_url.hostname != "agent-server":
            raise ValueError("agent_server_base_url_must_use_internal_agent_server")
        return cls(
            environment=environment,
            external_mode=external_mode,
            session_cookie_name=values.get(
                "TRAVEL_AGENT_SESSION_COOKIE_NAME", "travel_agent_session"
            ),
            session_cookie_secure=session_cookie_secure,
            session_cookie_samesite=session_cookie_samesite,
            csrf_cookie_name=values.get("TRAVEL_AGENT_CSRF_COOKIE_NAME", "travel_agent_csrf"),
            csrf_header_name=values.get("TRAVEL_AGENT_CSRF_HEADER_NAME", "X-CSRF-Token"),
            csrf_enabled=_read_bool(values, "TRAVEL_AGENT_CSRF_ENABLED", default=True),
            privacy_features_enabled=_read_bool(
                values, "TRAVEL_AGENT_PRIVACY_FEATURES_ENABLED", default=True
            ),
            session_ttl_seconds=_read_positive_int(
                values, "TRAVEL_AGENT_SESSION_TTL_SECONDS", default=8 * 60 * 60
            ),
            login_failure_limit=_read_positive_int(
                values, "TRAVEL_AGENT_LOGIN_FAILURE_LIMIT", default=5
            ),
            login_failure_window_seconds=_read_positive_int(
                values, "TRAVEL_AGENT_LOGIN_FAILURE_WINDOW_SECONDS", default=15 * 60
            ),
            database_url=values.get(
                "DATABASE_URL",
                "postgresql+asyncpg://travel_agent_dev:local_development_only@postgres:5432/travel_agent_dev",
            ),
            database_migration_url=values.get(
                "DATABASE_MIGRATION_URL",
                "postgresql+psycopg://travel_agent_dev:local_development_only@postgres:5432/travel_agent_dev",
            ),
            data_encryption_key_file=values.get(
                "TRAVEL_AGENT_DATA_ENCRYPTION_KEY_FILE", "/run/secrets/data_encryption_key"
            ),
            persistence_enabled=_read_bool(
                values, "TRAVEL_AGENT_PERSISTENCE_ENABLED", default=False
            ),
            cors_allowed_origins=origins,
            agent_server_base_url=agent_server_base_url.rstrip("/"),
            api_agent_token_file=values.get(
                "API_AGENT_INTERNAL_TOKEN_FILE", "/run/secrets/api_agent_internal_token"
            ),
            provider_registration=ProviderRegistration(
                provider_key=values.get("TRAVEL_AGENT_PROVIDER_KEY", ""),
                version=values.get("TRAVEL_AGENT_PROVIDER_VERSION", ""),
                base_url=values.get("TRAVEL_AGENT_PROVIDER_BASE_URL", ""),
                secret_ref=values.get("TRAVEL_AGENT_PROVIDER_SECRET_REF", ""),
            ),
            provider_approved=_read_bool(values, "TRAVEL_AGENT_PROVIDER_APPROVED", default=False),
        )

    def require_readonly_provider(self) -> ProviderRegistration:
        """在尝试真实调用前强制校验 Provider 完整性和明确审批状态。"""
        self.provider_registration.require_complete()
        if not self.provider_approved:
            raise ProviderConfigurationError("provider_not_approved")
        return self.provider_registration


def _read_bool(values: Mapping[str, str], key: str, default: bool) -> bool:
    """读取严格布尔环境变量，防止拼写错误意外关闭安全功能。"""
    value = values.get(key)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError(f"{key.lower()}_must_be_boolean")


def _read_origins(raw_origins: str) -> tuple[str, ...]:
    """读取精确 CORS Origin 白名单，不接受通配符或非 HTTP 协议。"""
    origins = tuple(
        origin.strip().rstrip("/") for origin in raw_origins.split(",") if origin.strip()
    )
    if not origins or "*" in origins:
        raise ValueError("cors_allowed_origins_invalid")
    for origin in origins:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("cors_origin_must_be_http_or_https")
    return origins


def _read_positive_int(values: Mapping[str, str], key: str, default: int) -> int:
    """读取严格正整数配置，避免会话或限流策略被错误地关闭。"""
    raw_value = values.get(key)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{key.lower()}_must_be_positive_integer") from error
    if value <= 0:
        raise ValueError(f"{key.lower()}_must_be_positive_integer")
    return value
