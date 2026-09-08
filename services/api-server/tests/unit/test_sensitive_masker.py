# 本文件验证统一 SensitiveMasker 的规则和值级脱敏行为。
# 定义文本、KV 秘钥、递归 JSON 和边界值测试函数。
from travel_agent_sensitive_masker import SensitiveMasker

from travel_agent_api.core.redaction import mask_sensitive_value


def test_sensitive_masker_masks_credentials_and_personal_data() -> None:
    """统一工具必须覆盖凭据、身份号码、银行卡、手机号和邮箱。"""
    masker = SensitiveMasker()
    text = (
        "sk-abc123xyz token: abc123 13812345678 "
        "310101200001011234 6222021234567890 user@example.com"
    )

    assert masker.mask_text(text) == (
        "******** token: ****** 138****5678 "
        "310101********1234 6222********7890 u***@example.com"
    )


def test_sensitive_masker_recurses_and_preserves_keys() -> None:
    """递归 JSON 中的敏感字段保留键名但不保留值，普通文本继续按规则脱敏。"""
    value = {
        "password": "secret-value",
        "profile": {"phone": "13812345678", "email": "user@example.com"},
        "items": [{"token": "abc"}, "6222021234567890"],
    }

    masked = mask_sensitive_value(value)

    assert masked == {
        "password": "******",
        "profile": {"phone": "138****5678", "email": "u***@example.com"},
        "items": [{"token": "******"}, "6222********7890"],
    }
