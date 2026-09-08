# 本文件定义 Agent Server 的关联标识与接口审计中间件。
# 定义 install_observability_middleware，用于创建上下文、回传关联头并写入脱敏审计日志。
from __future__ import annotations

import time

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from travel_agent_agent.core.audit import create_audit_logger, log_interface_event
from travel_agent_agent.core.correlation import (
    bind_correlation_context,
    create_correlation_context,
    reset_correlation_context,
)
from travel_agent_agent.core.redaction import summarize_request, summarize_response

_LOGGER = create_audit_logger()


def install_observability_middleware(application: FastAPI) -> None:
    """为每个 Agent HTTP 请求建立关联上下文并输出脱敏审计摘要。"""

    @application.middleware("http")
    async def observability_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """执行下游处理并记录请求/响应摘要，不记录提示词或认证信息。"""
        context = create_correlation_context(request.headers)
        request.state.correlation_context = context
        request_body = await request.body()
        started_at = time.perf_counter()
        token = bind_correlation_context(context)
        try:
            response = await call_next(request)
        finally:
            reset_correlation_context(token)
        for header_name, header_value in context.response_headers().items():
            response.headers[header_name] = header_value
        log_interface_event(
            _LOGGER,
            context,
            request.method,
            request.url.path,
            response.status_code,
            int((time.perf_counter() - started_at) * 1000),
            summarize_request(request_body, request.query_params),
            summarize_response(response.status_code, response.headers.get("content-type")),
        )
        return response
