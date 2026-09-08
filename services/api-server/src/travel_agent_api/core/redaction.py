# 本文件定义 API Server 的请求摘要与敏感字段脱敏规则。
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
    """返回不包含请求正文值、认证头或查询参数值的可诊断摘要。"""
    field_names, redacted_fields = _extract_body_fields(body)
    return {
        "content_length": len(body),
        "field_names": field_names,
        "redacted_fields": redacted_fields,
        "query_field_names": _safe_field_names(query_params.keys()),
        "query_redacted_fields": _sensitive_field_names(query_params.keys()),
    }


def summarize_response(status_code: int, content_type: str | None) -> dict[str, object]:
    """返回响应状态与内容类型，不读取或记录响应正文。"""
    return {"status_code": status_code, "content_type": content_type or ""}


def _extract_body_fields(body: bytes) -> tuple[list[str], list[str]]:
    """仅从 JSON 对象提取字段名，不保留任意字段的原始值。"""
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
    """返回非敏感字段名，避免因字段值而泄露用户数据。"""
    return sorted(name for name in _normalized_field_names(field_names) if not _is_sensitive(name))


def _sensitive_field_names(field_names: Iterable[object]) -> list[str]:
    """返回敏感字段名的固定脱敏标记，而不保留其值。"""
    return sorted(
        "<redacted>" for name in _normalized_field_names(field_names) if _is_sensitive(name)
    )


def _normalized_field_names(field_names: Iterable[object]) -> list[str]:
    """将字段名转换为有限长度的字符串，防止日志体积被恶意放大。"""
    return [str(name)[:64] for name in field_names]


def _is_sensitive(field_name: str) -> bool:
    """判断字段名是否命中固定敏感词，敏感字段始终不记录其值。"""
    normalized = field_name.lower()
    return any(marker in normalized for marker in _SENSITIVE_FIELD_MARKERS)
