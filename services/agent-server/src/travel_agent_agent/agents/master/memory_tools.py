# 文件职责：定义差旅偏好长期记忆的写入与召回工具。
# 定义 PreferenceMemoryTools，只允许记录非敏感偏好，未配置时安全降级。
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import StructuredTool
from travel_agent_sensitive_masker import SensitiveMasker

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.infrastructure.memory_client import BailianMemoryClient

MAX_MEMORY_CONTENT_LENGTH = 2000
MAX_MEMORY_QUERY_LENGTH = 1000


class PreferenceMemoryTools:
    """提供用户级差旅偏好的记录与召回工具。"""

    def __init__(
        self,
        client: BailianMemoryClient,
        context_provider: Callable[[], AgentContext | None],
    ) -> None:
        """保存记忆客户端与当前请求上下文读取函数。"""
        self.client = client
        self.context_provider = context_provider
        self.masker = SensitiveMasker()

    async def record_to_memory(self, content: str) -> dict[str, Any]:
        """记录用户明确表达的差旅偏好，拒绝缺少身份或空内容。"""
        context = self.context_provider()
        if context is None or not context.user_id:
            return {"success": False, "message": "缺少用户上下文，未保存记忆。"}
        text = content.strip() if isinstance(content, str) else ""
        if not text:
            return {"success": False, "message": "记录内容不能为空。"}
        result = await self.client.record(
            context.user_id, self.masker.mask_text(text)[:MAX_MEMORY_CONTENT_LENGTH]
        )
        if result.get("available") is False:
            return {"success": False, "message": result.get("message", "长期记忆不可用。")}
        return {"success": True, "message": "已记录该差旅偏好。"}

    async def retrieve_from_memory(self, query: str) -> dict[str, Any]:
        """召回当前用户的差旅偏好节点；未配置时返回空召回。"""
        context = self.context_provider()
        if context is None or not context.user_id:
            return {"memories": "", "message": "缺少用户上下文，未召回记忆。"}
        text = query.strip() if isinstance(query, str) else ""
        if not text:
            return {"memories": "", "message": "查询内容不能为空。"}
        result = await self.client.retrieve(
            context.user_id, self.masker.mask_text(text)[:MAX_MEMORY_QUERY_LENGTH]
        )
        memories = result.get("memories")
        if not isinstance(memories, str):
            memories = ""
        return {
            "memories": memories[:MAX_MEMORY_CONTENT_LENGTH],
            "available": result.get("available", True),
            "message": (
                result.get("message", "已返回长期记忆召回结果。")
                if memories
                else "长期记忆中没有相关偏好记录。"
            ),
        }

    def as_tools(self) -> list[StructuredTool]:
        """返回长期记忆写入与召回工具。"""
        return [
            StructuredTool.from_function(
                coroutine=self.record_to_memory,
                name="record_to_memory",
                description=(
                    "记录用户明确表达的差旅偏好（常飞航司、酒店品牌、座位与出发时段等）；"
                    "禁止记录姓名、电话、证件号与金额等敏感信息。"
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.retrieve_from_memory,
                name="retrieve_from_memory",
                description="召回与当前话题相关的历史差旅偏好，用于个性化推荐。",
            ),
        ]
