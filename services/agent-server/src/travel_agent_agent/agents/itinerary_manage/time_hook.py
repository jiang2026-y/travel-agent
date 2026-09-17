# 本文件实现行程管理与预订 Agent 共用的动态中国时间注入中间件。
# 定义 DynamicTimeInjectionHook，在每次模型调用前注入第二条 SYSTEM 时间消息。
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import SystemMessage

_TIME_ZONE = ZoneInfo("Asia/Shanghai")
_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


class DynamicTimeInjectionHook(AgentMiddleware):
    """在静态系统提示词之后注入当前中国日期，不持久化到会话消息。"""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        """注入可替换时钟，便于稳定测试相对日期。"""
        self._clock = clock or (lambda: datetime.now(_TIME_ZONE))

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """保留调用前 Hook 边界；实际消息排序由模型调用包装器保证。"""
        del state, runtime
        return None

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """在当前模型请求中插入唯一动态时间 SYSTEM 消息。"""
        return handler(self._with_dynamic_time(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步模型调用同样在静态提示词之后插入动态时间。"""
        return await handler(self._with_dynamic_time(request))

    def _with_dynamic_time(self, request: ModelRequest) -> ModelRequest:
        """移除上一轮瞬态时间消息并将最新时间置于用户消息之前。"""
        messages = [message for message in request.messages if message.id != "dynamic_time"]
        dynamic = SystemMessage(content=self.render_time_message(), id="dynamic_time")
        first_system = next(
            (
                index
                for index, message in enumerate(messages)
                if isinstance(message, SystemMessage)
            ),
            None,
        )
        if first_system is None:
            return request.override(messages=[dynamic, *messages])
        return request.override(
            messages=[
                *messages[: first_system + 1],
                dynamic,
                *messages[first_system + 1 :],
            ]
        )

    def render_time_message(self) -> str:
        """生成不包含时分秒的中国业务日历时间消息。"""
        current = _as_china_time(self._clock())
        return (
            f"当前日期：{current.date().isoformat()}（{_WEEKDAYS[current.weekday()]}）。\n"
            "当前时区：Asia/Shanghai。\n"
            "请以此时间为准，并严格按照时间处理规则解释相对日期。"
        )


def _as_china_time(value: datetime) -> datetime:
    """将时钟值视为或转换为 Asia/Shanghai，避免测试使用朴素时间时受主机影响。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=_TIME_ZONE)
    return value.astimezone(_TIME_ZONE)
