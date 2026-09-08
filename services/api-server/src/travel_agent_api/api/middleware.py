# 本文件定义 API Server 的关联标识与接口审计中间件。
# 定义 install_observability_middleware，用于创建上下文、回传关联头并写入脱敏审计日志。

import time

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from travel_agent_api.core.audit import create_audit_logger, log_interface_event
from travel_agent_api.core.correlation import create_correlation_context
from travel_agent_api.core.redaction import summarize_request, summarize_response

_LOGGER = create_audit_logger()


def install_observability_middleware(application: FastAPI) -> None:
    """为每个 HTTP 请求强制建立关联上下文并记录安全审计摘要。"""

    @application.middleware("http")
    async def observability_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """读取请求摘要，执行下游处理并记录结果；不记录任何原始正文或认证头。"""
        context = create_correlation_context(request.headers)
        request.state.correlation_context = context
        request_body = await request.body()
        started_at = time.perf_counter()
        response = await call_next(request)
        for header_name, header_value in context.response_headers().items():
            response.headers[header_name] = header_value
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        log_interface_event(
            logger=_LOGGER,
            context=context,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            request_summary=summarize_request(request_body, request.query_params),
            response_summary=summarize_response(
                response.status_code, response.headers.get("content-type")
            ),
        )
        return response
