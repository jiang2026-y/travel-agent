# 文件职责：把工具调用过程发布为安全的进度事件，供前端展示"跑到哪一步"。
# 定义 ToolProgressMiddleware、build_tool_progress_middleware 与事件构造辅助函数：
# 在工具调用前后发布 tool_started / tool_completed（工具名、状态、耗时），
# 不包含任何工具参数与结果正文；无诊断回调时不发事件、不影响执行。
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest

from travel_agent_agent.agents.base import AgentContext

TOOL_STARTED_EVENT = "tool_started"
TOOL_COMPLETED_EVENT = "tool_completed"
# 子智能体调度与用户澄清属于流程节点，已有专门事件，这里排除以避免时间线噪音。
EXCLUDED_TOOLS = frozenset(
    {"itinerary_manage_agent", "booking_agent", "info_agent", "ask_user"}
)
FAILED_STATUSES = frozenset({"provider_error", "failed", "error", "unavailable"})


class ToolProgressMiddleware(AgentMiddleware):
    """按工具调用发布进度事件；事件发布失败不影响工具执行。"""

    def __init__(
        self, agent_name: str, context_getter: Callable[[], AgentContext | None]
    ) -> None:
        """保存对外展示的 Agent 名称与当前请求上下文读取函数。"""
        super().__init__()
        self.agent_name = agent_name
        self._context_getter = context_getter

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        """发布开始事件，执行工具，再按结果发布完成或失败事件。"""
        tool_name = tool_call_name(request)
        if tool_name in EXCLUDED_TOOLS:
            return await handler(request)
        started_at = time.monotonic()
        await self._publish(TOOL_STARTED_EVENT, {"agent": self.agent_name, "tool": tool_name})
        try:
            result = await handler(request)
        except BaseException:
            await self._publish(
                TOOL_COMPLETED_EVENT,
                {
                    "agent": self.agent_name,
                    "tool": tool_name,
                    "status": "failed",
                    "duration_ms": _elapsed_ms(started_at),
                },
            )
            raise
        await self._publish(
            TOOL_COMPLETED_EVENT,
            {
                "agent": self.agent_name,
                "tool": tool_name,
                "status": tool_result_status(result),
                "duration_ms": _elapsed_ms(started_at),
            },
        )
        return result

    async def _publish(self, event_type: str, data: dict[str, object]) -> None:
        """经当前请求的诊断回调发布事件；缺失或异常时静默跳过。"""
        context = self._context_getter()
        callback = getattr(context, "diagnostic_callback", None) if context else None
        if callback is None:
            return
        try:
            await callback(event_type, data)
        except Exception:
            # 进度事件是旁路能力，任何发布失败都不应影响工具执行。
            return


def tool_call_name(request: ToolCallRequest) -> str:
    """从工具调用请求中读取工具名，缺失时返回空串。"""
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, dict):
        name = tool_call.get("name")
        if isinstance(name, str):
            return name
    return str(getattr(getattr(request, "tool", None), "name", "") or "")


def tool_result_status(result: Any) -> str:
    """把工具结果归一为 completed 或 failed，供前端标记进度状态。"""
    status = getattr(result, "status", None)
    if status == "error":
        return "failed"
    content = getattr(result, "content", result)
    if isinstance(content, dict):
        if content.get("success") is False or content.get("available") is False:
            return "failed"
        if str(content.get("status", "")).lower() in FAILED_STATUSES:
            return "failed"
    return "completed"


def build_tool_progress_middleware(
    agent_name: str, context_getter: Callable[[], AgentContext | None]
) -> list[AgentMiddleware]:
    """返回指定 Agent 的工具进度中间件列表。"""
    return [ToolProgressMiddleware(agent_name, context_getter)]


def _elapsed_ms(started_at: float) -> int:
    """返回自开始以来的毫秒数，向下取整且不为负。"""
    return max(0, int((time.monotonic() - started_at) * 1000))
