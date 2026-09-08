# 本文件验证 PostgreSQL 账号认证、会话/Run 写入和审计事实的最小闭环。
# 定义 test_postgres_login_run_and_audit_flow，使用临时账号并在测试结束后清理其数据。
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from travel_agent_api.core.settings import Settings
from travel_agent_api.main import create_app

pytestmark = pytest.mark.skipif(
    os.environ.get("TRAVEL_AGENT_RUN_POSTGRES_TESTS") != "true",
    reason="requires a host-accessible local PostgreSQL instance",
)


def test_postgres_login_run_and_audit_flow() -> None:
    """验证持久化模式从账号认证到加密消息、Run 和审计查询均可工作。"""
    suffix = uuid.uuid4().hex
    user_id = f"test_user_{suffix}"
    admin_id = f"test_admin_{suffix}"
    user_account = f"test.user.{suffix}"
    admin_account = f"test.admin.{suffix}"
    password = "temporary-test-password"
    asyncio.run(_create_users(user_id, admin_id, user_account, admin_account, password))
    try:
        with TestClient(create_app(_persistent_settings())) as client:
            login = client.post(
                "/api/v1/auth/login", json={"account": user_account, "password": password}
            )
            assert login.status_code == 200
            csrf = login.cookies["travel_agent_csrf"]
            started = client.post(
                "/api/v1/conversations/runs",
                json={"message": "查询北京到上海的航班"},
                headers={"X-CSRF-Token": csrf},
            )
            assert started.status_code == 202
            run_id = started.json()["run_id"]
            assert client.get(f"/api/v1/runs/{run_id}").json()["status"] == "queued"
            assert client.get("/api/v1/conversations").json()["conversations"]

            admin_client = TestClient(create_app(_persistent_settings()))
            admin_login = admin_client.post(
                "/api/v1/auth/login", json={"account": admin_account, "password": password}
            )
            assert admin_login.status_code == 200
            entries = admin_client.get("/api/v1/admin/audit").json()["entries"]
            assert any(entry["event_type"] == "run_created" for entry in entries)
    finally:
        asyncio.run(_delete_users(user_id, admin_id))


def _persistent_settings() -> Settings:
    """构造连接本地 Docker PostgreSQL 的测试配置，不读取真实外部 Provider。"""
    root = Path(__file__).resolve().parents[4]
    return Settings.from_environment(
        {
            "TRAVEL_AGENT_ENV": "development",
            "TRAVEL_AGENT_EXTERNAL_MODE": "real_readonly",
            "DATABASE_URL": "postgresql+asyncpg://travel_agent_dev:local_development_only@localhost:5432/travel_agent_dev",
            "TRAVEL_AGENT_DATA_ENCRYPTION_KEY_FILE": str(root / ".secrets" / "data_encryption_key"),
            "TRAVEL_AGENT_PERSISTENCE_ENABLED": "true",
        }
    )


async def _create_users(
    user_id: str, admin_id: str, user_account: str, admin_account: str, password: str
) -> None:
    """插入本测试专用的用户和管理员账号。"""
    engine = create_async_engine(_persistent_settings().database_url)
    password_hash = PasswordHasher().hash(password)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (user_id, username, password_hash, role) "
                    "VALUES (:user_id, :username, :password_hash, :role)"
                ),
                [
                    {
                        "user_id": user_id,
                        "username": user_account,
                        "password_hash": password_hash,
                        "role": "user",
                    },
                    {
                        "user_id": admin_id,
                        "username": admin_account,
                        "password_hash": password_hash,
                        "role": "admin",
                    },
                ],
            )
    finally:
        await engine.dispose()


async def _delete_users(user_id: str, admin_id: str) -> None:
    """按外键依赖顺序删除测试产生的数据，避免保留测试账号或消息。"""
    engine = create_async_engine(_persistent_settings().database_url)
    user_ids = [user_id, admin_id]
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM audit_events WHERE user_id = ANY(:user_ids)"),
                {"user_ids": user_ids},
            )
            await connection.execute(
                text(
                    "DELETE FROM messages WHERE conversation_id IN "
                    "(SELECT conversation_id FROM conversations WHERE user_id = ANY(:user_ids))"
                ),
                {"user_ids": user_ids},
            )
            await connection.execute(
                text("DELETE FROM runs WHERE user_id = ANY(:user_ids)"), {"user_ids": user_ids}
            )
            await connection.execute(
                text("DELETE FROM conversations WHERE user_id = ANY(:user_ids)"),
                {"user_ids": user_ids},
            )
            await connection.execute(
                text("DELETE FROM users WHERE user_id = ANY(:user_ids)"), {"user_ids": user_ids}
            )
    finally:
        await engine.dispose()
