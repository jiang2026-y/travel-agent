# 本文件定义 Tool Gateway 的关联标识与脱敏接口审计中间件。
# 定义 create_audit_logger，用于配置审计标准输出。
# 定义 install_observability_middleware，用于回传关联头并记录不含认证信息或正文值的请求摘要。
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint
from travel_agent_sensitive_masker import SensitiveMasker

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MASKER = SensitiveMasker()


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """保存 Tool Gateway 调用链需要的关联标识。"""

    trace_id: str
    request_id: str
    run_id: str
    thread_id: str

    def response_headers(self) -> dict[str, str]:
        """返回给内部调用方的关联标识响应头。"""
        return {
            "X-Trace-Id": self.trace_id,
            "X-Request-Id": self.request_id,
            "X-Run-Id": self.run_id,
            "X-Thread-Id": self.thread_id,
        }


def create_audit_logger() -> logging.Logger:
    """创建幂等的标准输出审计 logger，供 Docker 日志采集安全查询。"""
    logger = logging.getLogger("travel_agent_tool_gateway.audit")
    logger.setLevel(logging.INFO)
    if not any(getattr(handler, "_travel_agent_audit", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._travel_agent_audit = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    return logger


def install_observability_middleware(application: FastAPI) -> None:
    """为 Gateway 的每个 HTTP 接口安装关联和脱敏审计中间件。"""

    logger = create_audit_logger()

    @application.middleware("http")
    async def observability_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """记录路径、耗时和无值字段摘要；绝不记录 Authorization 或正文值。"""
        context = _create_correlation_context(request.headers)
        request_body = await request.body()
        started_at = time.perf_counter()
        response = await call_next(request)
        for header_name, header_value in context.response_headers().items():
            response.headers[header_name] = header_value
        logger.info(
            "interface_audit %s",
            json.dumps(
                _MASKER.mask_log_event(
                    {
                    "event": "interface_audit",
                    "trace_id": context.trace_id,
                    "request_id": context.request_id,
                    "run_id": context.run_id,
                    "thread_id": context.thread_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": int((time.perf_counter() - started_at) * 1000),
                    "request": {"content_length": len(request_body), "field_names": []},
                    "response": {
                        "status_code": response.status_code,
                        "content_type": response.headers.get("content-type", ""),
                    },
                    }
                ),
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return response


def _create_correlation_context(headers: Mapping[str, str]) -> CorrelationContext:
    """透传合法关联标识，缺失或非法时生成新的随机标识。"""
    return CorrelationContext(
        trace_id=_read_or_create(headers, "x-trace-id"),
        request_id=_read_or_create(headers, "x-request-id"),
        run_id=_read_or_create(headers, "x-run-id"),
        thread_id=_read_or_create(headers, "x-thread-id"),
    )


def _read_or_create(headers: Mapping[str, str], header_name: str) -> str:
    """过滤潜在日志注入标识，保障关联字段长度与字符集。"""
    value = headers.get(header_name, "")
    return value if _SAFE_IDENTIFIER.fullmatch(value) else uuid.uuid4().hex
