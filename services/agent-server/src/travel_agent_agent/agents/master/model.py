# 本文件创建仅通过 Tool Gateway 访问百炼的 MasterAgent LangChain 聊天模型。
# 定义 create_master_chat_model 和 _read_gateway_token，固定 qwen3.7-plus 并关闭思考模式。
from __future__ import annotations

from pathlib import Path

from langchain_openai import ChatOpenAI

from travel_agent_agent.core.settings import Settings


def create_master_chat_model(settings: Settings) -> ChatOpenAI:
    """创建 qwen3.7-plus 非思考模型，调用路径仅指向内部 Tool Gateway。"""
    return ChatOpenAI(
        model="qwen3.7-plus",
        temperature=0,
        api_key=_read_gateway_token(settings.agent_gateway_token_file),
        base_url=f"{settings.tool_gateway_base_url}/internal/v1/dashscope",
        extra_body={"enable_thinking": False},
        timeout=70.0,
        max_retries=0,
    )


def _read_gateway_token(path: str) -> str:
    """读取 Agent 到 Tool Gateway 的内部 Secret，不允许回退到环境变量。"""
    token = Path(path).read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise ValueError("agent_gateway_token_invalid")
    return token
