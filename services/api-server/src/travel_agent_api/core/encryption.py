# 本文件实现 PostgreSQL 高敏感字段的最小 AES-256-GCM 加密能力。
# 定义 EncryptedPayload、DataEncryptionError 与 DataEncryptionService，
# 分别表示密文载荷、配置错误和加解密服务。
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class DataEncryptionError(RuntimeError):
    """表示数据加密密钥或密文不符合最小安全约束。"""


@dataclass(frozen=True, slots=True)
class EncryptedPayload:
    """保存数据库持久化所需的密文、随机 nonce 与密钥版本。"""

    ciphertext: bytes
    nonce: bytes
    key_version: int


@dataclass(frozen=True, slots=True)
class DataEncryptionService:
    """使用单个 Docker Secret 中的 32 字节密钥执行 AES-256-GCM 加解密。"""

    key: bytes
    key_version: int = 1

    def __post_init__(self) -> None:
        """拒绝不是 AES-256 所需长度的密钥或非法版本号。"""
        if len(self.key) != 32:
            raise DataEncryptionError("data_encryption_key_must_be_32_bytes")
        if self.key_version < 1:
            raise DataEncryptionError("data_encryption_key_version_invalid")

    @classmethod
    def from_secret_file(
        cls, secret_file: str | Path, key_version: int = 1
    ) -> DataEncryptionService:
        """从 Docker Secret 或本机忽略文件读取 Base64 编码的 32 字节密钥。"""
        try:
            encoded_key = Path(secret_file).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise DataEncryptionError("data_encryption_key_file_unavailable") from error
        try:
            key = base64.b64decode(encoded_key, validate=True)
        except ValueError as error:
            raise DataEncryptionError("data_encryption_key_not_base64") from error
        return cls(key=key, key_version=key_version)

    def encrypt_json(self, value: dict[str, Any]) -> EncryptedPayload:
        """序列化敏感对象并以新随机 nonce 加密，禁止调用方复用 nonce。"""
        plaintext = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.key).encrypt(nonce, plaintext, None)
        return EncryptedPayload(ciphertext=ciphertext, nonce=nonce, key_version=self.key_version)

    def decrypt_json(self, encrypted: EncryptedPayload) -> dict[str, Any]:
        """解密并校验 JSON 对象；密钥版本不匹配时拒绝读取。"""
        if encrypted.key_version != self.key_version:
            raise DataEncryptionError("data_encryption_key_version_unsupported")
        try:
            plaintext = AESGCM(self.key).decrypt(encrypted.nonce, encrypted.ciphertext, None)
            decoded = json.loads(plaintext.decode("utf-8"))
        except (InvalidTag, UnicodeDecodeError, ValueError) as error:
            raise DataEncryptionError("data_encryption_payload_invalid") from error
        if not isinstance(decoded, dict):
            raise DataEncryptionError("data_encryption_payload_must_be_object")
        return decoded
