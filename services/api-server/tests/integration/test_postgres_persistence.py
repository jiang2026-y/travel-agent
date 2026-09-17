# 本文件验证 PostgreSQL 账号认证、会话/Run 写入、同会话续轮和审计事实的最小闭环。
# 定义 test_postgres_login_run_and_audit_flow、test_postgres_turn_reuses_thread
# 与 test_postgres_travel_orders_expose_approval_status，
# 使用临时账号并在测试结束后清理其数据。
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


def test_postgres_turn_reuses_thread_within_same_conversation() -> None:
    """同一会话内开启新一轮必须复用 thread_id、不新建会话，并刷新会话更新时间。"""
    suffix = uuid.uuid4().hex
    user_id = f"test_turn_{suffix}"
    admin_id = f"test_turn_admin_{suffix}"
    user_account = f"test.turn.{suffix}"
    admin_account = f"test.turn.admin.{suffix}"
    password = "temporary-test-password"
    asyncio.run(_create_users(user_id, admin_id, user_account, admin_account, password))
    try:
        with TestClient(create_app(_persistent_settings())) as client:
            login = client.post(
                "/api/v1/auth/login", json={"account": user_account, "password": password}
            )
            csrf = login.cookies["travel_agent_csrf"]
            headers = {"X-CSRF-Token": csrf}
            first = client.post(
                "/api/v1/conversations/runs",
                json={"message": "查看北京到上海的航班"},
                headers=headers,
            )
            assert first.status_code == 202
            conversation_id = first.json()["conversation_id"]
            thread_id = first.json()["thread_id"]
            before = client.get("/api/v1/conversations").json()["conversations"]

            second = client.post(
                "/api/v1/conversations/runs",
                json={"message": "就订第一个", "conversation_id": conversation_id},
                headers=headers,
            )
            after = client.get("/api/v1/conversations").json()["conversations"]
            foreign = client.post(
                "/api/v1/conversations/runs",
                json={"message": "继续", "conversation_id": "conv_missing"},
                headers=headers,
            )
            messages = client.get(f"/api/v1/conversations/{conversation_id}/messages")

        assert second.status_code == 202
        assert second.json()["run_id"] != first.json()["run_id"]
        assert second.json()["conversation_id"] == conversation_id
        assert second.json()["thread_id"] == thread_id
        assert len(after) == len(before) == 1
        assert after[0]["updated_at"] >= before[0]["updated_at"]
        assert foreign.status_code == 404
        assert foreign.json()["error"]["code"] == "conversation_not_found"
        assert messages.status_code == 200
        assert len(messages.json()["messages"]) >= 2
    finally:
        asyncio.run(_delete_users(user_id, admin_id))


def test_postgres_travel_orders_expose_approval_status() -> None:
    """真实库验证：差旅单列表按 approval_id 关联审批实例并带出审批状态。"""
    suffix = uuid.uuid4().hex
    user_id = f"test_order_{suffix}"
    admin_id = f"test_order_admin_{suffix}"
    user_account = f"test.order.{suffix}"
    admin_account = f"test.order.admin.{suffix}"
    password = "temporary-test-password"
    order_id = f"order_int_{suffix}"
    process_instance_id = f"approval_int_{suffix}"
    asyncio.run(_create_users(user_id, admin_id, user_account, admin_account, password))
    try:
        asyncio.run(_create_order_with_approval(user_id, order_id, process_instance_id))
        with TestClient(create_app(_persistent_settings())) as client:
            login = client.post(
                "/api/v1/auth/login", json={"account": user_account, "password": password}
            )
            assert login.status_code == 200
            listed = client.get("/api/v1/travel-orders")

        assert listed.status_code == 200
        order = next(
            item for item in listed.json()["orders"] if item["order_id"] == order_id
        )
        # 关键断言：审批状态必须来自 travel_order.approval_id 关联的审批实例，
        # 而不是差旅单自身状态；写错关联列时该断言会失败。
        assert order["approval_id"] == process_instance_id
        assert order["approval_status"] == "PENDING"
        assert order["submitted_at"] is not None
    finally:
        asyncio.run(_cleanup_travel_records(user_id, order_id, process_instance_id))
        asyncio.run(_delete_users(user_id, admin_id))


async def _create_order_with_approval(
    user_id: str, order_id: str, process_instance_id: str
) -> None:
    """写入一条已提交差旅单及其待审批实例，供只读接口联调使用。"""
    engine = create_async_engine(_persistent_settings().database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO travel_order "
                    "(order_id, user_id, departure_city, destination, departure_date, "
                    " return_date, purpose, status, approval_id) "
                    "VALUES (:order_id, :user_id, '北京', '南京', DATE '2026-09-25', "
                    " DATE '2026-09-27', '集成测试', 'SUBMITTED', :approval_id)"
                ),
                {"order_id": order_id, "user_id": user_id, "approval_id": process_instance_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO approval_record "
                    "(process_instance_id, user_id, title, status, order_id, submit_time) "
                    "VALUES (:pid, :user_id, '集成测试差旅审批', 'PENDING', :order_id, now())"
                ),
                {"pid": process_instance_id, "user_id": user_id, "order_id": order_id},
            )
    finally:
        await engine.dispose()


async def _cleanup_travel_records(
    user_id: str, order_id: str, process_instance_id: str
) -> None:
    """删除本测试写入的审批实例与差旅单，避免污染共享开发库。"""
    engine = create_async_engine(_persistent_settings().database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM approval_record "
                    "WHERE process_instance_id = :pid AND user_id = :user_id"
                ),
                {"pid": process_instance_id, "user_id": user_id},
            )
            await connection.execute(
                text("DELETE FROM travel_order WHERE order_id = :oid AND user_id = :user_id"),
                {"oid": order_id, "user_id": user_id},
            )
    finally:
        await engine.dispose()


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
