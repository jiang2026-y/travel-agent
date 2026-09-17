# 文件职责：提供同一 Run/恢复链内工具调用的非敏感会话缓存。
# 定义 SessionToolCache，缓存政策 JSON、档案完整度摘要和常驻城市，并用 asyncio.Lock 协调并发回源。
from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

ResultT = TypeVar("ResultT")


@dataclass(slots=True)
class SessionToolCache:
    """保存同一 Agent 会话内可安全复用的工具结果，不保存档案明文。"""

    travel_policy_by_city: dict[str, str] = field(default_factory=dict)
    user_contact_info_summary: dict[str, Any] | None = None
    user_base_location: str | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def get_travel_policy(self, city: str | None) -> str | None:
        """按非空城市读取已缓存的政策 JSON。"""
        if not city:
            return None
        async with self._lock:
            return self.travel_policy_by_city.get(city)

    async def set_travel_policy(self, city: str | None, policy: str | None) -> None:
        """仅缓存非空城市和非空政策结果。"""
        if not city or not policy or not policy.strip():
            return
        async with self._lock:
            self.travel_policy_by_city[city] = policy

    async def get_user_contact_info(self) -> dict[str, Any] | None:
        """读取非敏感档案完整度摘要的副本。"""
        async with self._lock:
            return copy.deepcopy(self.user_contact_info_summary)

    async def set_user_contact_info(self, summary: dict[str, Any] | None) -> None:
        """写入非敏感完整度摘要，拒绝空值。"""
        if not summary:
            return
        async with self._lock:
            self.user_contact_info_summary = copy.deepcopy(summary)

    async def invalidate_user_contact_info(self) -> None:
        """档案更新后主动失效联系资料摘要，防止后续读取脏数据。"""
        async with self._lock:
            self.user_contact_info_summary = None

    async def get_user_base_location(self) -> str | None:
        """读取已缓存的非空常驻城市。"""
        async with self._lock:
            return self.user_base_location

    async def set_user_base_location(self, location: str | None) -> None:
        """写入或覆盖常驻城市；空值不作为缓存命中保存。"""
        if not location or not location.strip():
            return
        async with self._lock:
            self.user_base_location = location.strip()

    async def load_travel_policy(
        self,
        city: str | None,
        loader: Callable[[], Awaitable[tuple[str | None, ResultT]]],
    ) -> tuple[ResultT | None, bool]:
        """在单把锁内执行政策 cache-aside，避免同城并发调用重复回源。"""
        if not city:
            _, loaded = await loader()
            return loaded, False
        async with self._lock:
            cached = self.travel_policy_by_city.get(city)
            if cached is not None:
                return cached, True  # type: ignore[return-value]
            serialized, loaded = await loader()
            if serialized and serialized.strip():
                self.travel_policy_by_city[city] = serialized
            return loaded, False

    async def load_user_contact_info(
        self, loader: Callable[[], Awaitable[dict[str, Any]]]
    ) -> tuple[dict[str, Any], bool]:
        """在锁内执行联系档案摘要 cache-aside，返回副本避免调用方修改缓存。"""
        async with self._lock:
            if self.user_contact_info_summary is not None:
                return copy.deepcopy(self.user_contact_info_summary), True
            result = await loader()
            if result:
                self.user_contact_info_summary = copy.deepcopy(result)
            return result, False

    async def load_user_base_location(
        self, loader: Callable[[], Awaitable[str | None]]
    ) -> tuple[str | None, bool]:
        """在锁内执行常驻城市 cache-aside，空值不进入缓存。"""
        async with self._lock:
            if self.user_base_location is not None:
                return self.user_base_location, True
            location = await loader()
            if location and location.strip():
                self.user_base_location = location.strip()
            return location, False
