# 本文件验证 Agent Server 的环境、安全和真实只读 Provider 配置。
# 定义 Tool Gateway 地址、隐私开关和 Provider 完整性拒绝规则测试。
import pytest

from travel_agent_agent.core.settings import ProviderConfigurationError, Settings


def test_agent_defaults_use_internal_gateway_and_privacy_protection() -> None:
    """Agent 默认只能经内部 Tool Gateway 调用工具，并启用隐私保护开关。"""
    settings = Settings.from_environment(
        {"TRAVEL_AGENT_ENV": "development", "TRAVEL_AGENT_EXTERNAL_MODE": "real_readonly"}
    )

    assert settings.tool_gateway_base_url == "http://tool-gateway:8002"
    assert settings.api_agent_token_file == "/run/secrets/api_agent_internal_token"
    assert settings.privacy_features_enabled is True
    assert settings.provider_call_enabled is False


def test_agent_rejects_partial_provider_registration() -> None:
    """Provider 只填写部分信息时，Agent 不得尝试任何真实外部调用。"""
    settings = Settings.from_environment(
        {
            "TRAVEL_AGENT_ENV": "development",
            "TRAVEL_AGENT_EXTERNAL_MODE": "real_readonly",
            "TRAVEL_AGENT_PROVIDER_KEY": "bailian",
            "TRAVEL_AGENT_PROVIDER_BASE_URL": "https://dashscope.aliyuncs.com",
        }
    )

    with pytest.raises(ProviderConfigurationError, match="provider_configuration_incomplete"):
        settings.require_readonly_provider()
