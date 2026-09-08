# 文件职责：保存当前请求范围内的差旅会话上下文，不保存 Token 或敏感正文。
# 定义 SessionCtx、bind_session_context、get_session_context 和 reset_session_context。
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace

from travel_agent_agent.agents.common.tool_cache import SessionToolCache


@dataclass(frozen=True, slots=True)
class SessionCtx:
    """保存用户、会话、Run、线程和当前差旅单标识。"""

    user_id: str = ""
    conversation_id: str = ""
    run_id: str = ""
    thread_id: str = ""
    travel_order_id: str | None = None
    profile_revision: int = 0
    flight_complete: bool | None = None
    hotel_complete: bool | None = None
    train_complete: bool | None = None
    base_city: str | None = None
    tool_cache: SessionToolCache = field(default_factory=SessionToolCache, repr=False)

    def with_travel_order(self, order_id: str) -> SessionCtx:
        """返回绑定指定差旅单的新上下文。"""
        return replace(self, travel_order_id=order_id)

    def with_profile_status(
        self, flight_complete: bool, hotel_complete: bool, train_complete: bool
    ) -> SessionCtx:
        """返回携带非敏感档案完整度的新上下文。"""
        return replace(
            self,
            profile_revision=self.profile_revision + 1,
            flight_complete=flight_complete,
            hotel_complete=hotel_complete,
            train_complete=train_complete,
        )

    def with_base_city(self, base_city: str | None) -> SessionCtx:
        """返回携带常驻城市的新上下文。"""
        return replace(self, base_city=base_city)


_SESSION_CONTEXT: ContextVar[SessionCtx] = ContextVar(
    "travel_agent_session_context", default=SessionCtx()
)


def bind_session_context(context: SessionCtx) -> Token[SessionCtx]:
    """绑定当前请求的会话上下文。"""
    return _SESSION_CONTEXT.set(context)


def get_session_context() -> SessionCtx:
    """读取当前请求上下文。"""
    return _SESSION_CONTEXT.get()


def set_travel_order_id(order_id: str) -> SessionCtx:
    """在当前请求上下文中绑定差旅单 ID，并返回更新后的上下文。"""
    updated = _SESSION_CONTEXT.get().with_travel_order(order_id)
    _SESSION_CONTEXT.set(updated)
    return updated


def set_profile_status(
    flight_complete: bool, hotel_complete: bool, train_complete: bool
) -> SessionCtx:
    """刷新当前请求的非敏感档案完整度缓存。"""
    updated = _SESSION_CONTEXT.get().with_profile_status(
        flight_complete, hotel_complete, train_complete
    )
    _SESSION_CONTEXT.set(updated)
    return updated


def set_base_city(base_city: str | None) -> SessionCtx:
    """刷新当前请求的常驻城市缓存。"""
    updated = _SESSION_CONTEXT.get().with_base_city(base_city)
    _SESSION_CONTEXT.set(updated)
    return updated


def reset_session_context(token: Token[SessionCtx]) -> None:
    """恢复调用前的上下文，避免跨请求污染。"""
    _SESSION_CONTEXT.reset(token)
