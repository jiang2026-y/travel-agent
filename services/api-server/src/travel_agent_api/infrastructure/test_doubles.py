# 本文件定义 API Server 仅供自动化测试使用的内存替身。
# 定义 PreconfiguredUser、InMemoryUserStore、InMemorySessionStore 与 InMemoryRunStore。
# 这些定义用于测试账号、会话和 Run 索引。
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PreconfiguredUser:
    """表示不含真实凭据的预置测试用户。"""

    user_id: str
    account: str
    role: str


@dataclass(slots=True)
class InMemoryUserStore:
    """保存测试用户，不连接数据库也不保存密码。"""

    users: dict[str, PreconfiguredUser] = field(default_factory=dict)

    def add(self, user: PreconfiguredUser) -> None:
        """加入一个预置测试用户。"""
        self.users[user.user_id] = user

    def get(self, user_id: str) -> PreconfiguredUser | None:
        """按用户标识读取测试用户。"""
        return self.users.get(user_id)

    def get_by_account(self, account: str) -> PreconfiguredUser | None:
        """按企业账号读取测试用户，不返回任何密码或哈希值。"""
        return next((user for user in self.users.values() if user.account == account), None)


@dataclass(slots=True)
class InMemorySessionStore:
    """保存测试会话与归属关系。"""

    sessions: dict[str, str] = field(default_factory=dict)
    expires_at: dict[str, float] = field(default_factory=dict)

    def bind(self, session_id: str, user_id: str, ttl_seconds: int | None = None) -> None:
        """绑定会话到用户。"""
        self.sessions[session_id] = user_id
        if ttl_seconds is not None:
            self.expires_at[session_id] = time.monotonic() + ttl_seconds

    def get_user_id(self, session_id: str) -> str | None:
        """返回会话所属用户。"""
        expires_at = self.expires_at.get(session_id)
        if expires_at is not None and expires_at <= time.monotonic():
            self.invalidate(session_id)
            return None
        return self.sessions.get(session_id)

    def invalidate(self, session_id: str) -> None:
        """使一个测试会话失效。"""
        self.sessions.pop(session_id, None)
        self.expires_at.pop(session_id, None)


@dataclass(slots=True)
class InMemoryRunStore:
    """保存测试 Run 的状态字符串。"""

    statuses: dict[str, str] = field(default_factory=dict)

    def save_status(self, run_id: str, status: str) -> None:
        """保存 Run 状态。"""
        self.statuses[run_id] = status

    def get_status(self, run_id: str) -> str | None:
        """读取 Run 状态。"""
        return self.statuses.get(run_id)
