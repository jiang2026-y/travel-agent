# 本文件封装 P0 内存服务端会话操作。
# 定义 SessionService，用于创建、读取和失效会话，隔离认证服务与底层会话存储。
from __future__ import annotations

import uuid
from dataclasses import dataclass

from travel_agent_api.infrastructure.test_doubles import InMemorySessionStore


@dataclass(slots=True)
class SessionService:
    """管理 P0 内存会话 ID 与过期时间，不存储浏览器密码或 Token。"""

    store: InMemorySessionStore
    ttl_seconds: int

    def create(self, user_id: str) -> str:
        """创建随机会话 ID，并按配置写入会话过期时间。"""
        session_id = uuid.uuid4().hex
        self.store.bind(session_id, user_id, self.ttl_seconds)
        return session_id

    def get_user_id(self, session_id: str | None) -> str | None:
        """读取有效会话归属；缺失或过期会话均返回空。"""
        return self.store.get_user_id(session_id) if session_id else None

    def invalidate(self, session_id: str | None) -> None:
        """使存在的会话失效，不暴露会话此前是否存在。"""
        if session_id:
            self.store.invalidate(session_id)
