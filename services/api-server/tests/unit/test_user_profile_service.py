# 本文件验证用户档案完整度、性别规范化和敏感字段校验规则。
# 定义机票/酒店/火车完整度、性别和通用证件测试。
from __future__ import annotations

import pytest

from travel_agent_api.application.user_profile_service import (
    UserProfileError,
    _normalize_field,
    contact_readiness,
    normalize_gender,
)


def test_contact_readiness_uses_five_flight_fields() -> None:
    """机票完整度必须包含中文姓名、证件类型、证件号、手机和性别。"""
    result = contact_readiness(
        {
            "chinese_name": "张三",
            "id_type": "PASSPORT",
            "id_number": "E12345678",
            "phone": "13812345678",
            "name_pinyin": "ZHANG SAN",
            "email": "user@example.com",
        },
        "M",
    )

    assert result["flightComplete"] is True
    assert result["hotelComplete"] is True
    assert result["trainComplete"] is True


def test_contact_readiness_returns_only_missing_fields() -> None:
    """不完整档案只提示当前缺少字段，不返回已有敏感值。"""
    result = contact_readiness({"chinese_name": "张三"}, None)

    assert result["flightComplete"] is False
    assert result["missingFields"]["flight"] == ["证件类型", "证件号", "手机号", "性别"]
    assert "张三" not in result["message"]


@pytest.mark.parametrize(("raw", "expected"), [("男", "M"), ("女", "F"), ("m", "M"), ("F", "F")])
def test_normalize_gender_accepts_chinese_and_storage_values(raw: str, expected: str) -> None:
    """性别应接受中文表达和数据库 M/F 表达。"""
    assert normalize_gender(raw) == expected


def test_general_document_allows_passport_and_rejects_short_number() -> None:
    """通用证件支持护照形式，过短号码应被拒绝。"""
    assert _normalize_field("id_number", "E12345678") == "E12345678"
    with pytest.raises(UserProfileError, match="id_number_invalid"):
        _normalize_field("id_number", "123")
