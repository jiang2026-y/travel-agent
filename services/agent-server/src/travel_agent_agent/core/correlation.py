# 本文件定义 Agent Server 的请求关联标识。
# 定义 CorrelationContext，用于保存调用链标识。
# 定义 create_correlation_context 与 get_correlation_context，用于创建和读取上下文。
# 定义请求/响应头方法，用于向上下游透传上下文。
from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import cast

from starlette.requests import Request

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ACTIVE_CONTEXT: ContextVar[CorrelationContext | None] = ContextVar(
    "agent_correlation", default=None
)


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """保存一次 Agent 调用链所需的关联标识。"""

    trace_id: str
    request_id: str
    run_id: str
    thread_id: str

    def request_headers(self) -> dict[str, str]:
        """返回传递给下游内部服务的四类关联标识请求头。"""
        return {
            "X-Trace-Id": self.trace_id,
            "X-Request-Id": self.request_id,
            "X-Run-Id": self.run_id,
            "X-Thread-Id": self.thread_id,
        }

    def response_headers(self) -> dict[str, str]:
        """返回应回传给上游 API 的关联标识响应头。"""
        return self.request_headers()


def create_correlation_context(headers: Mapping[str, str]) -> CorrelationContext:
    """透传合法上游标识；缺失或非法时生成新的随机标识。"""
    return CorrelationContext(
        trace_id=_read_or_create(headers, "x-trace-id"),
        request_id=_read_or_create(headers, "x-request-id"),
        run_id=_read_or_create(headers, "x-run-id"),
        thread_id=_read_or_create(headers, "x-thread-id"),
    )


def get_correlation_context(request: Request) -> CorrelationContext:
    """从请求状态读取由中间件创建的关联上下文。"""
    return cast(CorrelationContext, request.state.correlation_context)


def bind_correlation_context(context: CorrelationContext) -> Token[CorrelationContext | None]:
    """绑定当前协程的关联上下文，供受控下游 HTTP 客户端透传四类标识。"""
    return _ACTIVE_CONTEXT.set(context)


def reset_correlation_context(token: Token[CorrelationContext | None]) -> None:
    """在请求结束时恢复上一个协程上下文，避免标识泄漏到其他请求。"""
    _ACTIVE_CONTEXT.reset(token)


def get_active_correlation_context() -> CorrelationContext | None:
    """返回当前协程的关联上下文；无 HTTP 请求时安全返回空值。"""
    return _ACTIVE_CONTEXT.get()


def _read_or_create(headers: Mapping[str, str], header_name: str) -> str:
    """阻止非法或超长标识进入日志与后续 Agent 调用。"""
    value = headers.get(header_name, "")
    return value if _SAFE_IDENTIFIER.fullmatch(value) else uuid.uuid4().hex
