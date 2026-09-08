# 本文件定义 Agent Server 的请求摘要与敏感字段脱敏规则。
# 定义 summarize_request，用于生成无正文值的安全请求摘要。
# 定义 summarize_response，用于生成响应元数据摘要。
# 定义 mask_sensitive_value，用于调用共享 SensitiveMasker 处理任意结构化值。
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from travel_agent_sensitive_masker import SensitiveMasker

_SENSITIVE_FIELD_MARKERS = (
    "password",
    "sms_code",
    "verification_code",
    "cookie",
    "authorization",
    "api_key",
    "token",
    "secret",
    "prompt",
    "thought",
    "chain_of_thought",
    "id_card",
    "passport",
    "payment",
)
_MASKER = SensitiveMasker()


def mask_sensitive_value(value: Any) -> Any:
    """使用共享 SensitiveMasker 递归处理结构化值。"""
    return _MASKER.mask_json(value)


def summarize_request(body: bytes, query_params: Mapping[str, str]) -> dict[str, object]:
    """返回不包含正文值、认证头或查询参数值的 Agent 请求摘要。"""
    field_names, redacted_fields = _extract_body_fields(body)
    return {
        "content_length": len(body),
        "field_names": field_names,
        "redacted_fields": redacted_fields,
        "query_field_names": _safe_field_names(query_params.keys()),
        "query_redacted_fields": _sensitive_field_names(query_params.keys()),
    }


def summarize_response(status_code: int, content_type: str | None) -> dict[str, object]:
    """返回响应元数据，不读取或记录模型响应正文。"""
    return {"status_code": status_code, "content_type": content_type or ""}


def _extract_body_fields(body: bytes) -> tuple[list[str], list[str]]:
    """仅从 JSON 对象读取字段名，绝不保留字段值。"""
    if not body:
        return [], []
    try:
        payload: Any = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], []
    if not isinstance(payload, dict):
        return [], []
    payload = mask_sensitive_value(payload)
    return _safe_field_names(payload.keys()), _sensitive_field_names(payload.keys())


def _safe_field_names(field_names: Iterable[object]) -> list[str]:
    """筛除敏感字段名，仅保留可诊断的非敏感名称。"""
    return sorted(name for name in _normalized_field_names(field_names) if not _is_sensitive(name))


def _sensitive_field_names(field_names: Iterable[object]) -> list[str]:
    """使用固定标记代替敏感字段名和值。"""
    return sorted(
        "<redacted>" for name in _normalized_field_names(field_names) if _is_sensitive(name)
    )


def _normalized_field_names(field_names: Iterable[object]) -> list[str]:
    """限制字段名长度，避免恶意输入放大日志。"""
    return [str(name)[:64] for name in field_names]


def _is_sensitive(field_name: str) -> bool:
    """判断字段名是否包含提示词、思维链、凭据或身份支付敏感语义。"""
    return any(marker in field_name.lower() for marker in _SENSITIVE_FIELD_MARKERS)
