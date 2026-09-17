# 文件职责：创建偏好解析专用的 qwen3.6-flash 裸模型（无工具、无思考）。
# 定义 create_preference_chat_model 与 _read_gateway_token，经受限 Tool Gateway 调用。
from __future__ import annotations

from pathlib import Path

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from travel_agent_agent.core.settings import Settings

PREFERENCE_MODEL_NAME = "qwen3.6-flash"


def create_preference_chat_model(settings: Settings) -> ChatOpenAI:
    """创建不带工具的 qwen3.6-flash 聊天模型，用于把记忆原文解析为结构化偏好。"""
    return ChatOpenAI(
        model=PREFERENCE_MODEL_NAME,
        temperature=0,
        api_key=SecretStr(_read_gateway_token(settings.agent_gateway_token_file)),
        base_url=f"{settings.tool_gateway_base_url}/internal/v1/dashscope",
        extra_body={"enable_thinking": False},
        timeout=15.0,
        max_retries=0,
    )


def _read_gateway_token(path: str) -> str:
    """读取 Agent 到 Tool Gateway 的内部 Secret。"""
    token = Path(path).read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise ValueError("agent_gateway_token_invalid")
    return token
