# 本文件实现基于 AgentBase 的问题改写 Agent，不包含 ReAct 工具循环。
# 定义 QueryRewriteResult 与 QueryRewriteAgent，负责一次改写并限制输出长度。
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from travel_agent_agent.agents.base import AgentBase, AgentContext


class RewriteModelClient(Protocol):
    """定义现有问题改写模型客户端的最小兼容接口。"""

    async def rewrite(self, text: str, history: str) -> str: ...


@dataclass(frozen=True, slots=True)
class QueryRewriteResult:
    """保存一次问题改写后的文本和是否发生变化。"""

    rewritten_text: str
    changed: bool


class QueryRewriteAgent(AgentBase[str, QueryRewriteResult]):
    """执行单次问题改写模型调用，不回答问题、不调用工具。"""

    name = "queryRewriteAgent"

    def __init__(self, client: RewriteModelClient) -> None:
        """注入现有改写客户端，复用百炼和 Tool Gateway 边界。"""
        self._client = client

    async def _execute(self, value: str, context: AgentContext) -> QueryRewriteResult:
        """调用模型并限制改写文本长度。"""
        rewritten = (await self._client.rewrite(value, context.context_summary)).strip()
        if len(rewritten) > 8000:
            raise ValueError("query_rewrite_too_long")
        return QueryRewriteResult(rewritten, rewritten != value.strip())

    async def rewrite(self, text: str, history: str) -> str:
        """兼容旧 RewritePort，返回改写文本。"""
        result = await self.run(text, AgentContext(trace_id="agent-local", context_summary=history))
        return result.rewritten_text
