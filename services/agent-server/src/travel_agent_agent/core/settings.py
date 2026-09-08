# 本文件定义 Agent Server 的环境与真实只读 Provider 配置。
# 定义 ProviderConfigurationError，用于拒绝不完整 Provider。
# 定义 ProviderRegistration，用于校验 Provider 登记；定义 Settings，用于读取安全配置。
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit


class ProviderConfigurationError(RuntimeError):
    """表示 Provider 因未登记、信息不完整或未审批而不能被 Agent 调用。"""


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    """保存 Provider 的稳定标识、版本、HTTPS 地址和密钥引用。"""

    provider_key: str
    version: str
    base_url: str
    secret_ref: str

    @property
    def has_any_value(self) -> bool:
        """判断 Provider 是否已有任意登记字段。"""
        return any((self.provider_key, self.version, self.base_url, self.secret_ref))

    @property
    def is_complete(self) -> bool:
        """判断 Provider 是否具备最小只读调用登记信息。"""
        return all((self.provider_key, self.version, self.base_url, self.secret_ref))

    def require_complete(self) -> None:
        """拒绝未登记、部分登记和非 HTTPS Provider 地址。"""
        if not self.has_any_value:
            raise ProviderConfigurationError("provider_not_configured")
        if not self.is_complete:
            raise ProviderConfigurationError("provider_configuration_incomplete")
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ProviderConfigurationError("provider_base_url_must_use_https")


@dataclass(frozen=True, slots=True)
class Settings:
    """保存 Agent 的运行环境、内部网关、隐私和 Provider 安全开关。"""

    environment: str
    external_mode: str
    api_server_base_url: str
    tool_gateway_base_url: str
    redis_url: str
    checkpoint_ttl_seconds: int
    hitl_token_ttl_seconds: int
    api_agent_token_file: str
    agent_gateway_token_file: str
    data_encryption_key_file: str
    privacy_features_enabled: bool
    provider_registration: ProviderRegistration
    provider_approved: bool

    @property
    def provider_call_enabled(self) -> bool:
        """仅在 Provider 信息完整且明确审批后标记为可申请调用。"""
        return self.provider_registration.is_complete and self.provider_approved

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """读取 Agent 环境变量，确保其只使用真实只读模式与内部 Tool Gateway。"""
        values = environ or os.environ
        external_mode = values.get("TRAVEL_AGENT_EXTERNAL_MODE", "real_readonly")
        if external_mode != "real_readonly":
            raise ValueError("external_mode_must_be_real_readonly")
        tool_gateway_base_url = values.get("TOOL_GATEWAY_BASE_URL", "http://tool-gateway:8002")
        parsed_gateway_url = urlsplit(tool_gateway_base_url)
        if parsed_gateway_url.scheme != "http" or parsed_gateway_url.hostname != "tool-gateway":
            raise ValueError("tool_gateway_base_url_must_use_internal_gateway")
        api_server_base_url = values.get("API_SERVER_BASE_URL", "http://api-server:8000")
        parsed_api_url = urlsplit(api_server_base_url)
        if parsed_api_url.scheme != "http" or parsed_api_url.hostname != "api-server":
            raise ValueError("api_server_base_url_must_use_internal_api_server")
        redis_url = values.get("TRAVEL_AGENT_REDIS_URL", "redis://redis:6379/0")
        parsed_redis_url = urlsplit(redis_url)
        if (
            parsed_redis_url.scheme not in {"redis", "rediss"}
            or parsed_redis_url.hostname != "redis"
        ):
            raise ValueError("redis_url_must_use_internal_redis")
        return cls(
            environment=values.get("TRAVEL_AGENT_ENV", "development"),
            external_mode=external_mode,
            api_server_base_url=api_server_base_url.rstrip("/"),
            tool_gateway_base_url=tool_gateway_base_url.rstrip("/"),
            redis_url=redis_url,
            checkpoint_ttl_seconds=_read_positive_int(
                values, "TRAVEL_AGENT_CHECKPOINT_TTL_SECONDS", default=7 * 24 * 60 * 60
            ),
            hitl_token_ttl_seconds=_read_positive_int(
                values, "TRAVEL_AGENT_HITL_TOKEN_TTL_SECONDS", default=15 * 60
            ),
            api_agent_token_file=values.get(
                "API_AGENT_INTERNAL_TOKEN_FILE", "/run/secrets/api_agent_internal_token"
            ),
            agent_gateway_token_file=values.get(
                "AGENT_GATEWAY_INTERNAL_TOKEN_FILE",
                "/run/secrets/agent_gateway_internal_token",
            ),
            data_encryption_key_file=values.get(
                "TRAVEL_AGENT_DATA_ENCRYPTION_KEY_FILE", "/run/secrets/data_encryption_key"
            ),
            privacy_features_enabled=_read_bool(
                values, "TRAVEL_AGENT_PRIVACY_FEATURES_ENABLED", default=True
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
        """在真实调用前强制校验 Provider 完整性和审批，不允许 Fake 回退。"""
        self.provider_registration.require_complete()
        if not self.provider_approved:
            raise ProviderConfigurationError("provider_not_approved")
        return self.provider_registration


def _read_bool(values: Mapping[str, str], key: str, default: bool) -> bool:
    """读取严格布尔环境变量，防止拼写错误关闭隐私或审批开关。"""
    value = values.get(key)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError(f"{key.lower()}_must_be_boolean")


def _read_positive_int(values: Mapping[str, str], key: str, default: int) -> int:
    """读取正整数环境变量，用于限制检查点等具有保留期限的运行参数。"""
    value = values.get(key)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{key.lower()}_must_be_positive_integer") from error
    if parsed <= 0:
        raise ValueError(f"{key.lower()}_must_be_positive_integer")
    return parsed
