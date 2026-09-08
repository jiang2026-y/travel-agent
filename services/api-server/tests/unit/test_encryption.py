# 本文件验证高敏感资料和消息正文的 AES-256-GCM 加密边界。
# 定义加解密成功、nonce 随机性、密钥长度和密钥版本拒绝测试。
import pytest

from travel_agent_api.core.encryption import (
    DataEncryptionError,
    DataEncryptionService,
    EncryptedPayload,
)


def test_encrypt_json_uses_random_nonce_and_round_trips() -> None:
    """相同敏感资料必须生成不同密文，并且仅持有相同密钥时可解密。"""
    service = DataEncryptionService(key=b"a" * 32)
    value = {"phone": "13812345678", "id_number": "310101200001011234"}

    first = service.encrypt_json(value)
    second = service.encrypt_json(value)

    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext
    assert service.decrypt_json(first) == value


def test_encryption_rejects_invalid_key_and_key_version() -> None:
    """不允许使用短密钥或以错误密钥版本解密敏感资料。"""
    with pytest.raises(DataEncryptionError, match="32_bytes"):
        DataEncryptionService(key=b"short")

    service = DataEncryptionService(key=b"a" * 32)
    encrypted = service.encrypt_json({"name_pinyin": "ZHANG SAN"})
    unsupported = EncryptedPayload(encrypted.ciphertext, encrypted.nonce, key_version=2)
    with pytest.raises(DataEncryptionError, match="version_unsupported"):
        service.decrypt_json(unsupported)
