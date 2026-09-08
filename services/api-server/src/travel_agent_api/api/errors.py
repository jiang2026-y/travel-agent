# 本文件定义 API Server 的安全错误信封。
# 定义 ServiceError，用于受控业务错误；定义 register_error_handlers，用于注册受控和未知异常处理器。

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from travel_agent_api.core.correlation import get_correlation_context

_LOGGER = logging.getLogger("travel_agent_api.errors")


class ServiceError(Exception):
    """表示可安全返回给调用方的受控服务错误。"""

    def __init__(self, code: str, message: str, retryable: bool, status_code: int) -> None:
        """创建含稳定错误码、可重试语义和 HTTP 状态的业务异常。"""
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


def register_error_handlers(application: FastAPI) -> None:
    """注册不会泄露堆栈、凭据或内部实现细节的异常处理器。"""

    @application.exception_handler(ServiceError)
    async def service_error_handler(request: Request, error: ServiceError) -> JSONResponse:
        """将受控异常转换为包含关联标识的统一错误信封。"""
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

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        """将参数校验失败转换为安全错误信封，不回显原始请求值。"""
        del error
        context = get_correlation_context(request)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_failed",
                    "message": "请求参数不符合要求",
                    "retryable": False,
                },
                "trace_id": context.trace_id,
                "request_id": context.request_id,
            },
        )

    @application.exception_handler(Exception)
    async def unknown_error_handler(request: Request, error: Exception) -> JSONResponse:
        """隐藏未知异常的内部细节，并记录仅含类型和关联标识的错误日志。"""
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
