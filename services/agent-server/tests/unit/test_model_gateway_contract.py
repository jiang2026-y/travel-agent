# 文件职责：验证各模型工厂发送的网关参数与 Tool Gateway 校验规则一致。
# 定义摘要模型不携带思考参数、信息模型带 Agent 名称头与标题模型参数的测试。
from __future__ import annotations

from travel_agent_agent.agents.master.model import (
    create_info_chat_model,
    create_master_chat_model,
    create_sub_agent_chat_model,
    create_summary_chat_model,
)
from travel_agent_agent.agents.master.title_model import create_title_chat_model
from travel_agent_agent.core.settings import Settings


def _settings(tmp_path) -> Settings:
    """构造带可读内部 Token 的最小 Agent 配置。"""
    token_file = tmp_path / "agent_gateway_internal_token"
    token_file.write_text("g" * 32, encoding="utf-8")
    return Settings.from_environment(
        {
            "TOOL_GATEWAY_BASE_URL": "http://tool-gateway:8002",
            "API_SERVER_BASE_URL": "http://api-server:8000",
            "TRAVEL_AGENT_REDIS_URL": "redis://redis:6379/0",
            "AGENT_GATEWAY_INTERNAL_TOKEN_FILE": str(token_file),
        }
    )


def _headers(model: object) -> dict[str, str]:
    """读取模型默认请求头。"""
    return dict(getattr(model, "default_headers", {}) or {})


def test_summary_model_omits_thinking_flag_for_glm(tmp_path) -> None:
    """glm-5.1 无工具路径禁止携带 enable_thinking，摘要模型必须省略该参数。"""
    summary = create_summary_chat_model(_settings(tmp_path))
    assert summary.model_name == "glm-5.1"
    assert "enable_thinking" not in (summary.extra_body or {})
    assert _headers(summary) == {}


def test_agent_models_carry_expected_headers_and_thinking_flag(tmp_path) -> None:
    """各 Agent 模型必须带对应名称头，并按网关要求关闭思考模式。"""
    settings = _settings(tmp_path)
    assert _headers(create_master_chat_model(settings)) == {"X-Agent-Name": "masterAgent"}
    assert _headers(create_sub_agent_chat_model(settings, "bookingAgent")) == {
        "X-Agent-Name": "bookingAgent"
    }
    info = create_info_chat_model(settings)
    assert info.model_name == "glm-5.1"
    assert _headers(info) == {"X-Agent-Name": "infoAgent"}
    assert (info.extra_body or {}).get("enable_thinking") is False
    title = create_title_chat_model(settings)
    assert title.model_name == "qwen3.6-flash"
    assert _headers(title) == {}
