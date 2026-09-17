# 本文件创建仅通过 Tool Gateway 访问百炼的各 Agent 聊天模型。
# 定义 create_master_chat_model、create_sub_agent_chat_model、create_info_chat_model、
# create_summary_chat_model 与 _read_gateway_token，统一关闭思考模式并带上 Agent 名称头。
from __future__ import annotations

from pathlib import Path

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from travel_agent_agent.core.settings import Settings

MASTER_MODEL_NAME = "qwen3.7-plus"
INFO_MODEL_NAME = "glm-5.1"
SUMMARY_MODEL_NAME = "glm-5.1"


def create_master_chat_model(settings: Settings) -> ChatOpenAI:
    """创建 qwen3.7-plus 非思考模型，调用路径仅指向内部 Tool Gateway。"""
    return _create_chat_model(settings, MASTER_MODEL_NAME, "masterAgent")


def create_sub_agent_chat_model(settings: Settings, agent_name: str) -> ChatOpenAI:
    """创建带子 Agent 名称头的 qwen3.7-plus 非思考模型。"""
    return _create_chat_model(settings, MASTER_MODEL_NAME, agent_name)


def create_info_chat_model(settings: Settings) -> ChatOpenAI:
    """创建 InfoAgent 使用的 glm-5.1 模型，仍经由内部 Tool Gateway。"""
    return _create_chat_model(settings, INFO_MODEL_NAME, "infoAgent")


def create_summary_chat_model(settings: Settings) -> ChatOpenAI:
    """创建记忆压缩摘要专用的 glm-5.1 模型，不带工具与思考模式。"""
    return _create_chat_model(settings, SUMMARY_MODEL_NAME, None, include_thinking_flag=False)


def _create_chat_model(
    settings: Settings,
    model_name: str,
    agent_name: str | None,
    *,
    include_thinking_flag: bool = True,
) -> ChatOpenAI:
    """按模型名与可选 Agent 名称头创建聊天模型，禁止直连外部地址。"""
    return ChatOpenAI(
        model=model_name,
        temperature=0,
        api_key=SecretStr(_read_gateway_token(settings.agent_gateway_token_file)),
        base_url=f"{settings.tool_gateway_base_url}/internal/v1/dashscope",
        default_headers=({"X-Agent-Name": agent_name} if agent_name else None),
        extra_body=({"enable_thinking": False} if include_thinking_flag else None),
        timeout=70.0,
        max_retries=0,
    )


def _read_gateway_token(path: str) -> str:
    """读取 Agent 到 Tool Gateway 的内部 Secret，不允许回退到环境变量。"""
    token = Path(path).read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise ValueError("agent_gateway_token_invalid")
    return token
