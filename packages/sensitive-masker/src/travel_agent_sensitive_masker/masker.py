# 本文件实现跨 API、Agent 和 Tool Gateway 共用的敏感信息脱敏工具。
# 定义 SensitiveMasker，用于 API Key、KV 秘钥、手机号、证件、银行卡、邮箱和递归 JSON 脱敏。
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


class SensitiveMasker:
    """按统一规则对日志、异常和结构化数据执行不可逆脱敏。"""

    _API_KEY = re.compile(r"sk-[A-Za-z0-9]{6,}")
    _KV_SECRET = re.compile(
        r"(?P<key>password|passwd|token|secret|api[_-]?key|authorization)"
        r"(?P<sep>\s*[:=]\s*)(?P<value>[^,;\s&]+)",
        re.IGNORECASE,
    )
    _ID_CARD = re.compile(r"(?<![0-9])[0-9]{17}[0-9Xx](?![0-9])")
    _BANK_CARD = re.compile(r"(?<![0-9])[0-9]{16,19}(?![0-9])")
    _PHONE = re.compile(r"(?<![0-9])1[3-9][0-9]{9}(?![0-9])")
    _EMAIL = re.compile(r"(?P<local>[A-Za-z0-9._%+-]+)@(?P<domain>[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
    _SENSITIVE_KEYS = re.compile(
        r"(?:password|passwd|token|secret|api[_-]?key|authorization|cookie|prompt|"
        r"thought|chain[_-]?of[_-]?thought|id[_-]?card|passport|bank[_-]?card|payment)",
        re.IGNORECASE,
    )

    def mask_text(self, text: str) -> str:
        """脱敏自由文本，按凭据、身份号码、银行卡、手机号、邮箱顺序处理。"""
        if not isinstance(text, str) or not text:
            return text
        masked = self._KV_SECRET.sub(self._mask_key_value, text)
        masked = self._API_KEY.sub("********", masked)
        masked = self._ID_CARD.sub(self._mask_id_card, masked)
        masked = self._BANK_CARD.sub(self._mask_bank_card, masked)
        masked = self._PHONE.sub(self._mask_phone, masked)
        return self._EMAIL.sub(self._mask_email, masked)

    def mask_mapping(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """递归脱敏映射；敏感字段保留字段名但完全遮盖字段值。"""
        return {str(key): self._mask_value(value, str(key)) for key, value in data.items()}

    def mask_json(self, payload: Any) -> Any:
        """递归脱敏字典、列表、元组和文本，保留非敏感值类型。"""
        return self._mask_value(payload, "")

    def mask_log_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """返回可安全写入结构化日志的脱敏事件副本。"""
        return self.mask_mapping(event)

    def _mask_value(self, value: Any, field_name: str) -> Any:
        """按字段名和运行时类型递归处理一个值。"""
        if self._SENSITIVE_KEYS.search(field_name):
            return "******" if value is not None else None
        if isinstance(value, Mapping):
            return self.mask_mapping(value)
        if isinstance(value, list):
            return [self._mask_value(item, "") for item in value]
        if isinstance(value, tuple):
            return tuple(self._mask_value(item, "") for item in value)
        if isinstance(value, str):
            return self.mask_text(value)
        return value

    @staticmethod
    def _mask_key_value(match: re.Match[str]) -> str:
        """保留 KV 秘钥名称和分隔符，遮盖值。"""
        return f"{match.group('key')}{match.group('sep')}******"

    @staticmethod
    def _mask_phone(match: re.Match[str]) -> str:
        """保留手机号前三位和后四位。"""
        value = match.group(0)
        return f"{value[:3]}****{value[-4:]}"

    @staticmethod
    def _mask_id_card(match: re.Match[str]) -> str:
        """保留身份证前六位和后四位。"""
        value = match.group(0)
        return f"{value[:6]}{'*' * (len(value) - 10)}{value[-4:]}"

    @staticmethod
    def _mask_bank_card(match: re.Match[str]) -> str:
        """保留银行卡前四位和后四位。"""
        value = match.group(0)
        return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"

    @staticmethod
    def _mask_email(match: re.Match[str]) -> str:
        """保留邮箱用户名首字符和完整域名。"""
        local = match.group("local")
        return f"{local[0]}***@{match.group('domain')}"
