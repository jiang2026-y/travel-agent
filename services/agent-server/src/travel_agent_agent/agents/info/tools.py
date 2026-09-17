# 文件职责：定义 InfoAgent 的知识库检索与目的地实时查询工具。
# 定义 KnowledgeRetrieveTools 与 DestinationLiveTools，全部只读且不接触外部密钥。
from __future__ import annotations

import time
from typing import Any, Protocol

from langchain_core.tools import StructuredTool

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.infrastructure.destination_live_client import DestinationLiveClient

MAX_KNOWLEDGE_NODES = 8


class KnowledgeRetriever(Protocol):
    """定义 InfoAgent 所需的最小只读知识检索接口。"""

    async def retrieve(self, query: str) -> dict[str, object]: ...


class KnowledgeRetrieveTools:
    """封装百炼知识库检索工具，并发布不泄露正文的检索诊断事件。"""

    def __init__(self, knowledge: KnowledgeRetriever, context: AgentContext) -> None:
        """保存知识检索客户端与当前请求上下文。"""
        self.knowledge = knowledge
        self.context = context

    async def retrieve_knowledge(self, query: str) -> dict[str, Any]:
        """检索差旅政策、注意事项与景点知识片段。"""
        callback = self.context.diagnostic_callback
        started_at = time.perf_counter()
        if callback is not None:
            await callback("knowledge_search_started", {"status": "running"})
        try:
            result = await self.knowledge.retrieve(query)
        except Exception as error:
            if callback is not None:
                await callback(
                    "knowledge_search_failed",
                    {
                        "status": "failed",
                        "error_code": _safe_knowledge_error_code(error),
                        "retryable": bool(getattr(error, "retryable", False)),
                        "duration_ms": _duration_ms(started_at),
                    },
                )
            return {
                "available": False,
                "nodes": [],
                "message": "知识库暂不可用，请如实告知用户稍后重试或联系管理员。",
            }
        nodes = result.get("nodes") if isinstance(result, dict) else None
        fragments = _normalize_nodes(nodes)
        if callback is not None:
            await callback(
                "knowledge_search_completed",
                {
                    "status": "completed" if fragments else "empty",
                    "count": len(fragments),
                    "sources": [item["source"] for item in fragments][:MAX_KNOWLEDGE_NODES],
                    "duration_ms": _duration_ms(started_at),
                },
            )
        if not fragments:
            return {
                "available": True,
                "nodes": [],
                "message": "知识库未检索到相关资料，请如实告知用户并建议换一种问法。",
            }
        return {"available": True, "nodes": fragments, "count": len(fragments)}

    def as_tools(self) -> list[StructuredTool]:
        """返回知识库检索工具。"""
        return [
            StructuredTool.from_function(
                coroutine=self.retrieve_knowledge,
                name="retrieve_knowledge",
                description=(
                    "检索差旅制度、差旅注意事项与景点知识库，用于通用规则类问题；"
                    "涉及当前用户个人标准的精确数字应改用政策查询工具。"
                ),
            )
        ]


class DestinationLiveTools:
    """封装目的地天气与当地资讯查询工具。"""

    def __init__(self, client: DestinationLiveClient) -> None:
        """保存经网关出网的目的地查询客户端。"""
        self.client = client

    async def query_weather(self, city: str, date: str | None = None) -> dict[str, Any]:
        """查询指定城市当天及未来两天天气，超出范围时返回可换工具的信号。"""
        return await self.client.query_weather(city, date)

    async def query_destination_news(
        self, city: str, topic: str | None = None
    ) -> dict[str, Any]:
        """查询目的地交通、活动、安全等当地资讯。"""
        return await self.client.query_destination_news(city, topic)

    def as_tools(self) -> list[StructuredTool]:
        """返回目的地实时查询工具集合。"""
        return [
            StructuredTool.from_function(
                coroutine=self.query_weather,
                name="query_weather",
                description=(
                    "联网查询目的地天气；支持今天及未来两天，超出范围会返回 beyond_range，"
                    "此时应改用更长期预报工具。"
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.query_destination_news,
                name="query_destination_news",
                description=(
                    "联网查询目的地最新资讯，topic 可选 traffic/event/safety/flight/"
                    "hotel/policy/general。"
                ),
            ),
        ]


def _normalize_nodes(nodes: object) -> list[dict[str, Any]]:
    """裁剪知识片段并限制数量，避免把上游原始报文塞进模型上下文。"""
    if not isinstance(nodes, list):
        return []
    fragments: list[dict[str, Any]] = []
    for node in nodes[:MAX_KNOWLEDGE_NODES]:
        if not isinstance(node, dict):
            continue
        text = node.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        source = node.get("source", "百炼知识库")
        fragments.append(
            {"text": text[:3000], "source": str(source)[:200], "score": node.get("score")}
        )
    return fragments


def _duration_ms(started_at: float) -> int:
    """将检索耗时转换为不含内部时间戳的整数毫秒。"""
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _safe_knowledge_error_code(error: Exception) -> str:
    """将知识库异常归一化为可展示的稳定错误码，拒绝透传 SDK 类型和正文。"""
    code = getattr(error, "code", None)
    if isinstance(code, str) and (code.startswith("dashscope_") or code.startswith("bailian_")):
        return code
    return "knowledge_search_failed"
