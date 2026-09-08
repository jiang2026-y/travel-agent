# 本文件实现账号密码认证、会话校验与登录失败限流。
# 定义 AuthenticatedUser、LoginRateLimiter 和用户目录协议/实现。
# 定义 AuthService，负责认证与会话建立。
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Protocol

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from travel_agent_api.application.session_service import SessionService
from travel_agent_api.infrastructure.test_doubles import InMemorySessionStore
from travel_agent_api.persistence.services import CredentialUser


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """表示可安全返回浏览器的最小身份信息。"""

    user_id: str
    account: str
    role: str

    @classmethod
    def from_user(cls, user: CredentialUser) -> AuthenticatedUser:
        """从认证凭据视图构造不包含密码哈希的公开身份。"""
        return cls(user_id=user.user_id, account=user.account, role=user.role)

    def to_public_dict(self) -> dict[str, str]:
        """返回浏览器可见的非敏感身份字段。"""
        return {"user_id": self.user_id, "account": self.account, "role": self.role}


class CredentialUserDirectory(Protocol):
    """定义认证服务查询账号与会话所属用户的异步边界。"""

    async def find_by_account(self, account: str) -> CredentialUser | None: ...

    async def find_by_user_id(self, user_id: str) -> CredentialUser | None: ...


@dataclass(slots=True)
class DevelopmentUserDirectory:
    """提供仅供自动化测试使用的内存预置账号目录。"""

    users: dict[str, CredentialUser]

    async def find_by_account(self, account: str) -> CredentialUser | None:
        """按账号读取预置测试用户。"""
        return next((user for user in self.users.values() if user.account == account), None)

    async def find_by_user_id(self, user_id: str) -> CredentialUser | None:
        """按用户标识读取预置测试用户。"""
        return self.users.get(user_id)


@dataclass(slots=True)
class LoginRateLimiter:
    """按账号和客户端 IP 保存固定窗口内的连续失败次数。"""

    attempts: dict[str, list[float]] = field(default_factory=dict)
    limit: int = 5
    window_seconds: int = 15 * 60

    def is_limited(self, account: str, client_ip: str) -> bool:
        """判断当前账号/IP 是否已超出失败阈值。"""
        return len(self._recent_attempts(self._key(account, client_ip))) >= self.limit

    def record_failure(self, account: str, client_ip: str) -> None:
        """记录一次失败并删除过期计数。"""
        key = self._key(account, client_ip)
        self.attempts[key] = [*self._recent_attempts(key), time.monotonic()]

    def clear(self, account: str, client_ip: str) -> None:
        """成功登录后清除该账号/IP 的连续失败计数。"""
        self.attempts.pop(self._key(account, client_ip), None)

    def _recent_attempts(self, key: str) -> list[float]:
        """返回仍在限流窗口内的失败时间点。"""
        cutoff = time.monotonic() - self.window_seconds
        return [occurred_at for occurred_at in self.attempts.get(key, []) if occurred_at >= cutoff]

    @staticmethod
    def _key(account: str, client_ip: str) -> str:
        """构造仅在内存使用的限流键。"""
        return f"{account}\x00{client_ip}"


@dataclass(slots=True)
class AuthService:
    """通过可替换用户目录完成认证，Cookie 会话仍由现有会话服务管理。"""

    users: CredentialUserDirectory
    session_service: SessionService
    limiter: LoginRateLimiter
    password_hasher: PasswordHasher = field(default_factory=PasswordHasher)

    @classmethod
    def create_development_service(
        cls, session_ttl_seconds: int, login_failure_limit: int, login_failure_window_seconds: int
    ) -> AuthService:
        """创建仅供测试的内存预置账号服务，不在 Docker 持久化模式使用。"""
        password_hasher = PasswordHasher()
        raw_users = (
            ("user_001", "travel.user", "user", "user-password"),
            ("admin_001", "travel.admin", "admin", "admin-password"),
        )
        users = {
            user_id: CredentialUser(
                user_id=user_id,
                account=account,
                role=role,
                password_hash=password_hasher.hash(password),
            )
            for user_id, account, role, password in raw_users
        }
        return cls(
            users=DevelopmentUserDirectory(users),
            session_service=SessionService(
                store=InMemorySessionStore(), ttl_seconds=session_ttl_seconds
            ),
            limiter=LoginRateLimiter(
                limit=login_failure_limit, window_seconds=login_failure_window_seconds
            ),
            password_hasher=password_hasher,
        )

    @classmethod
    def create_persistent_service(
        cls,
        users: CredentialUserDirectory,
        session_ttl_seconds: int,
        login_failure_limit: int,
        login_failure_window_seconds: int,
    ) -> AuthService:
        """创建使用 PostgreSQL 账号目录的认证服务，不注入任何默认账号。"""
        return cls(
            users=users,
            session_service=SessionService(
                store=InMemorySessionStore(), ttl_seconds=session_ttl_seconds
            ),
            limiter=LoginRateLimiter(
                limit=login_failure_limit, window_seconds=login_failure_window_seconds
            ),
        )

    async def login(
        self, account: str, password: str, client_ip: str
    ) -> tuple[AuthenticatedUser, str, str] | None:
        """验证凭据并建立会话，失败时统一返回空值以防止账号枚举。"""
        if self.limiter.is_limited(account, client_ip):
            return None
        user = await self.users.find_by_account(account)
        password_is_valid = user is not None and self._verify_password(user.password_hash, password)
        if user is None or not password_is_valid:
            self.limiter.record_failure(account, client_ip)
            return None
        self.limiter.clear(account, client_ip)
        session_id = self.session_service.create(user.user_id)
        csrf_token = secrets.token_urlsafe(32)
        return AuthenticatedUser.from_user(user), session_id, csrf_token

    async def get_authenticated_user(self, session_id: str | None) -> AuthenticatedUser | None:
        """从有效 Cookie 会话查询未被停用的用户。"""
        if not session_id:
            return None
        user_id = self.session_service.get_user_id(session_id)
        user = await self.users.find_by_user_id(user_id) if user_id else None
        return AuthenticatedUser.from_user(user) if user else None

    def logout(self, session_id: str | None) -> None:
        """使当前服务端会话失效。"""
        if session_id:
            self.session_service.invalidate(session_id)

    def _verify_password(self, password_hash: str, password: str) -> bool:
        """验证 Argon2id 哈希且不向浏览器传播异常细节。"""
        try:
            return self.password_hasher.verify(password_hash, password)
        except (VerificationError, VerifyMismatchError):
            return False
