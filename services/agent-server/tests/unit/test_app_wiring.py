# 文件职责：验证真实 Provider 配置下 Master 的子 Agent 注册与摘要模型装配。
# 定义子 Agent 注册范围测试，确保规划与审核保持禁用且三个已实现子 Agent 可装配。
from __future__ import annotations

from travel_agent_agent.agents.master.agent import MasterAgent
from travel_agent_agent.core.settings import Settings


def _settings(tmp_path) -> Settings:
    """构造已审批 Provider 的最小配置，避免任何外部网络访问。"""
    (tmp_path / "agent_gateway_internal_token").write_text("x" * 48, encoding="utf-8")
    return Settings.from_environment(
        {
            "TOOL_GATEWAY_BASE_URL": "http://tool-gateway:8002",
            "API_SERVER_BASE_URL": "http://api-server:8000",
            "TRAVEL_AGENT_REDIS_URL": "redis://redis:6379/0",
            "API_AGENT_INTERNAL_TOKEN_FILE": str(tmp_path / "api_agent_internal_token"),
            "AGENT_GATEWAY_INTERNAL_TOKEN_FILE": str(
                tmp_path / "agent_gateway_internal_token"
            ),
            "TRAVEL_AGENT_PROVIDER_KEY": "dashscope",
            "TRAVEL_AGENT_PROVIDER_VERSION": "intent-v1",
            "TRAVEL_AGENT_PROVIDER_BASE_URL": (
                "https://ws-afyh9lpghkjx1iz8.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
            ),
            "TRAVEL_AGENT_PROVIDER_SECRET_REF": "dashscope_api_key",
            "TRAVEL_AGENT_PROVIDER_APPROVED": "true",
            "TUNIU_API_KEY_FILE": str(tmp_path / "tuniu_api_key"),
        }
    )


def test_master_agent_factory_registers_three_sub_agents(tmp_path) -> None:
    """Master 只注册三个已实现子 Agent，规划与审核保持禁用。"""
    settings = _settings(tmp_path)
    master = MasterAgent.create_with_default_provider(
        object(),  # type: ignore[arg-type]
        settings=settings,
    )
    assert master.provider.enabled_configs and {
        config.key for config in master.provider.enabled_configs
    } == {"itinerary_manage_agent", "booking_agent", "info_agent"}
