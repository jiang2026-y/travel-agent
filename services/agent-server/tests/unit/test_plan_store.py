# 本文件验证加密规划 Redis 缓存的 Key、TTL、加密载荷与故障降级。
# 定义异步 Redis 替身及成功、空值、损坏数据测试。
from __future__ import annotations

import pytest

from travel_agent_agent.infrastructure.itinerary_plan_store import (
    ItineraryPlanStore,
    safe_user_cache_key,
)


class _Redis:
    """记录 Redis set/get 参数的异步替身。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expire: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int) -> bool:
        """保存缓存值与 TTL。"""
        self.values[key] = value
        self.expire[key] = ex
        return True

    async def get(self, key: str) -> str | None:
        """读取缓存值。"""
        return self.values.get(key)


@pytest.mark.asyncio
async def test_plan_store_encrypts_and_reads_with_ttl() -> None:
    """规划结果不以明文写入 Redis，读取后可恢复原文。"""
    redis = _Redis()
    store = ItineraryPlanStore(redis, b"k" * 32)  # type: ignore[arg-type]

    assert await store.save("user/a", '{"city":"上海"}') is True
    assert redis.expire["planner:result:user_a"] == 24 * 60 * 60
    assert "上海" not in redis.values["planner:result:user_a"]
    assert await store.load("user/a") == '{"city":"上海"}'


@pytest.mark.asyncio
async def test_plan_store_empty_and_corrupt_values_are_misses() -> None:
    """空值不写入，损坏缓存按未命中处理。"""
    redis = _Redis()
    store = ItineraryPlanStore(redis, b"k" * 32)  # type: ignore[arg-type]
    assert await store.save("user", " ") is False
    redis.values["planner:result:user"] = "corrupt"
    assert await store.load("user") is None


def test_safe_user_key_filters_and_defaults() -> None:
    """用户标识特殊字符应被替换，空值映射 default。"""
    assert safe_user_cache_key("a/b:c") == "a_b_c"
    assert safe_user_cache_key(" ") == "default"
