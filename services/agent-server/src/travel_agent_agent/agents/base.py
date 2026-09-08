# 本文件定义非 ReAct Agent 的统一执行基类和调用上下文。
# 定义 AgentContext 与 AgentBase，统一关联标识、Agent 名称和异步执行接口。
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generic, TypeVar

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class AgentContext:
    """保存一次 Agent 调用所需的关联标识和脱敏会话摘要。"""

    trace_id: str
    request_id: str = ""
    run_id: str = ""
    thread_id: str = ""
    conversation_id: str = ""
    user_id: str = ""
    role: str = "user"
    context_summary: str = ""


class AgentBase(ABC, Generic[InputT, OutputT]):
    """统一非 ReAct Agent 的元数据和异步执行入口。"""

    name: str = "agent"
    version: str = "v1"

    async def run(self, value: InputT, context: AgentContext) -> OutputT:
        """执行一次无工具循环的 Agent 调用。"""
        return await self._execute(value, context)

    @abstractmethod
    async def _execute(self, value: InputT, context: AgentContext) -> OutputT:
        """由具体 Agent 实现单次模型调用和结果校验。"""
        raise NotImplementedError
