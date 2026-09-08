# 本文件定义 API Server 的结构化接口审计日志写入器。
# 定义 create_audit_logger，用于配置审计标准输出。
# 定义 log_interface_event，用于写入关联标识、脱敏请求摘要、响应摘要和耗时。
from __future__ import annotations

import json
import logging

from travel_agent_api.core.correlation import CorrelationContext


def create_audit_logger() -> logging.Logger:
    """创建幂等的标准输出审计 logger，供 Docker 日志采集安全查询。"""
    logger = logging.getLogger("travel_agent_api.audit")
    logger.setLevel(logging.INFO)
    if not any(getattr(handler, "_travel_agent_audit", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._travel_agent_audit = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    return logger


def log_interface_event(
    logger: logging.Logger,
    context: CorrelationContext,
    method: str,
    path: str,
    status_code: int,
    duration_ms: int,
    request_summary: dict[str, object],
    response_summary: dict[str, object],
) -> None:
    """输出单行 JSON 审计日志，不接受任何原始认证信息或正文值。"""
    event = {
        "event": "interface_audit",
        "trace_id": context.trace_id,
        "request_id": context.request_id,
        "run_id": context.run_id,
        "thread_id": context.thread_id,
        "method": method,
        "path": path,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "request": request_summary,
        "response": response_summary,
    }
    logger.info("interface_audit %s", json.dumps(event, ensure_ascii=False, sort_keys=True))
