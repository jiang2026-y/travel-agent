# 本文件定义 API Server 的 PostgreSQL 连接边界。
# 定义 create_async_engine_from_settings、create_session_factory 和 metadata，
# 用于应用连接与 Alembic 导入。
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from travel_agent_api.core.settings import Settings
from travel_agent_api.persistence import models as _models

metadata = SQLModel.metadata


def create_async_engine_from_settings(settings: Settings) -> AsyncEngine:
    """按配置创建 PostgreSQL 异步引擎，不在导入模块时连接数据库。"""
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建请求范围外可注入的异步 SQLAlchemy Session 工厂。"""
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """提供提交/回滚边界；异常时回滚且不吞掉错误。"""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def model_registry() -> dict[str, Any]:
    """返回已注册的 P0 模型，供迁移审计和测试检查使用。"""
    model_names = (
        "User",
        "Conversation",
        "Message",
        "Run",
        "AuditEvent",
        "Trip",
        "TravelPolicyRule",
        "TravelOrder",
        "ApprovalRecord",
        "BookingRecord",
    )
    return {name: getattr(_models, name) for name in model_names}
