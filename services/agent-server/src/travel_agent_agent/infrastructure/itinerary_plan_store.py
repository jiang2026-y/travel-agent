# 文件职责：提供加密的 Redis 行程规划结果缓存。
# 定义 ItineraryPlanStore、InMemoryItineraryPlanStore 和安全 Key、密文、TTL 辅助方法。
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from redis.asyncio import Redis
from redis.exceptions import RedisError

_LOGGER = logging.getLogger("travel_agent_agent.itinerary_plan_cache")
_KEY_PREFIX = "planner:result:"
_TTL_SECONDS = 24 * 60 * 60
_SAFE_USER_ID = re.compile(r"[^a-zA-Z0-9_.-]")


@dataclass(slots=True)
class ItineraryPlanStore:
    """将规划结果以 AES-GCM 密文保存到 Redis，失败时安全降级为未命中。"""

    client: Redis
    key: bytes
    key_version: int = 1
    ttl_seconds: int = _TTL_SECONDS

    @classmethod
    def from_secret_file(cls, client: Redis, secret_file: str) -> ItineraryPlanStore:
        """从只读 Docker Secret 加载 Base64 编码的 AES-256 数据密钥。"""
        try:
            key = base64.b64decode(
                Path(secret_file).read_text(encoding="utf-8").strip(), validate=True
            )
        except (OSError, ValueError) as error:
            raise ValueError("itinerary_plan_cache_key_unavailable") from error
        if len(key) != 32:
            raise ValueError("itinerary_plan_cache_key_invalid")
        return cls(client=client, key=key)

    async def save(self, user_id: str | None, result_json: str | None) -> bool:
        """加密非空规划 JSON 后写入 Redis，异常不阻断主流程。"""
        if not result_json or not result_json.strip():
            return False
        try:
            envelope = self._encrypt(result_json)
            await self.client.set(self._key(user_id), envelope, ex=self.ttl_seconds)
            return True
        except (RedisError, ValueError):
            _LOGGER.warning("itinerary_plan_cache_write_failed")
            return False

    async def load(self, user_id: str | None) -> str | None:
        """读取并解密规划 JSON；缓存异常或损坏均按未命中处理。"""
        try:
            raw = await self.client.get(self._key(user_id))
            if not raw or not str(raw).strip():
                return None
            return self._decrypt(str(raw))
        except (RedisError, ValueError, InvalidTag, UnicodeDecodeError):
            _LOGGER.warning("itinerary_plan_cache_read_failed")
            return None

    def _encrypt(self, result_json: str) -> str:
        """以随机 nonce 加密 JSON 字符串并构造不含明文的 Redis 信封。"""
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.key).encrypt(nonce, result_json.encode("utf-8"), None)
        return json.dumps(
            {
                "v": self.key_version,
                "n": base64.b64encode(nonce).decode("ascii"),
                "c": base64.b64encode(ciphertext).decode("ascii"),
            },
            separators=(",", ":"),
        )

    def _decrypt(self, envelope: str) -> str:
        """验证版本并解密 Redis 信封，禁止返回非 UTF-8 结果。"""
        decoded = json.loads(envelope)
        if not isinstance(decoded, dict) or decoded.get("v") != self.key_version:
            raise ValueError("itinerary_plan_cache_envelope_invalid")
        nonce = base64.b64decode(decoded["n"], validate=True)
        ciphertext = base64.b64decode(decoded["c"], validate=True)
        return AESGCM(self.key).decrypt(nonce, ciphertext, None).decode("utf-8")

    @staticmethod
    def _key(user_id: str | None) -> str:
        """构造按安全用户标识隔离的 Redis Key。"""
        return f"{_KEY_PREFIX}{safe_user_cache_key(user_id)}"


@dataclass(slots=True)
class InMemoryItineraryPlanStore:
    """提供测试期内存规划缓存替身，不存储密钥或密文。"""

    values: dict[str, str] = field(default_factory=dict)

    async def save(self, user_id: str | None, result_json: str | None) -> bool:
        """保存非空测试结果。"""
        if not result_json or not result_json.strip():
            return False
        self.values[ItineraryPlanStore._key(user_id)] = result_json
        return True

    async def load(self, user_id: str | None) -> str | None:
        """读取测试缓存结果。"""
        return self.values.get(ItineraryPlanStore._key(user_id))


def safe_user_cache_key(user_id: str | None) -> str:
    """过滤 Redis Key 中的用户标识，空值稳定映射为 default。"""
    if not user_id or not user_id.strip():
        return "default"
    return _SAFE_USER_ID.sub("_", user_id)
