# 文件职责：统一构建基于 glm-5.1 的记忆压缩摘要中间件。
# 定义阈值常量与 build_summary_middleware，供 Master、行程管理、预订与信息 Agent 复用。
from __future__ import annotations

from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models import BaseChatModel

SUMMARY_MESSAGE_TRIGGER = 60
SUMMARY_TOKEN_TRIGGER = 98304
SUMMARY_KEEP_MESSAGES = 20


def build_summary_middleware(
    summary_model: BaseChatModel | None,
) -> SummarizationMiddleware | None:
    """按 Java AutoContextMemory 阈值构建摘要中间件；未提供模型时不启用。"""
    if summary_model is None:
        return None
    return SummarizationMiddleware(
        model=summary_model,
        trigger=[("messages", SUMMARY_MESSAGE_TRIGGER), ("tokens", SUMMARY_TOKEN_TRIGGER)],
        keep=("messages", SUMMARY_KEEP_MESSAGES),
    )
