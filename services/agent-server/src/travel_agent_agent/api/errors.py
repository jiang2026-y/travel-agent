# 本文件定义 Agent Server 的安全错误信封。
# 定义 ServiceError，用于受控 Agent 错误；定义 register_error_handlers，用于注册安全异常处理器。
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from travel_agent_agent.core.correlation import get_correlation_context

_LOGGER = logging.getLogger("travel_agent_agent.errors")


class ServiceError(Exception):
    """表示可安全返回给 API 的受控 Agent 错误。"""

    def __init__(self, code: str, message: str, retryable: bool, status_code: int) -> None:
        """创建含稳定错误码、可重试语义和 HTTP 状态的异常。"""
        self.code, self.message, self.retryable, self.status_code = (
            code,
            message,
            retryable,
            status_code,
        )


def register_error_handlers(application: FastAPI) -> None:
    """注册不泄露模型、工具或堆栈细节的异常处理器。"""

    @application.exception_handler(ServiceError)
    async def service_error_handler(request: Request, error: ServiceError) -> JSONResponse:
        """将受控异常转换为统一、可关联的错误信封。"""
        context = get_correlation_context(request)
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                },
                "trace_id": context.trace_id,
                "request_id": context.request_id,
            },
        )

    @application.exception_handler(Exception)
    async def unknown_error_handler(request: Request, error: Exception) -> JSONResponse:
        """隐藏未知异常的内部实现细节，只记录错误类型和关联标识。"""
        context = get_correlation_context(request)
        _LOGGER.error(
            "unexpected_error trace_id=%s request_id=%s error_type=%s",
            context.trace_id,
            context.request_id,
            type(error).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "系统暂时不可用",
                    "retryable": False,
                },
                "trace_id": context.trace_id,
                "request_id": context.request_id,
            },
        )
