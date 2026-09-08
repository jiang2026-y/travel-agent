# 本文件配置 Alembic 在线和离线迁移环境。
# 定义 run_migrations_offline 与 run_migrations_online，均读取显式迁移连接串并导入 SQLModel 元数据。
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 允许从仓库根目录直接执行 Alembic；容器中该路径对应工作区内的服务源码。
service_src = Path(__file__).resolve().parents[2] / "services" / "api-server" / "src"
if str(service_src) not in sys.path:
    sys.path.insert(0, str(service_src))

from travel_agent_api.persistence.database import metadata  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _migration_url() -> str:
    """读取迁移专用 PostgreSQL URL，防止误用异步应用连接串执行 DDL。"""
    url = os.environ.get("DATABASE_MIGRATION_URL", "")
    if not url:
        raise RuntimeError("database_migration_url_required")
    return url


def run_migrations_offline() -> None:
    """生成离线 SQL；不建立数据库连接。"""
    context.configure(
        url=_migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """建立短连接执行迁移；连接池使用 NullPool 避免一次性容器遗留连接。"""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _migration_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
