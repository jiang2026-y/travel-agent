# API Server PostgreSQL 迁移说明

本目录只保存 API Server 的 Alembic 迁移。首个迁移仅创建 P0 的 `users`、`conversations`、`messages`、`runs`、`audit_events`、`trips` 与 `travel_policy_rules`，不创建订单、预订、审批、支付、用户 API Key 或 LangGraph 业务检查点表。

Docker Compose 使用 `db-migrate` 一次性服务执行迁移；API Server 仅在该服务成功结束后启动。

本机执行前，先确保 PostgreSQL 已启动，并设置迁移连接串：

```powershell
$env:DATABASE_MIGRATION_URL='postgresql+psycopg://travel_agent_dev:local_development_only@localhost:5432/travel_agent_dev'
uv run alembic -c migrations/api-server/alembic.ini upgrade head
```

查看当前版本：

```powershell
uv run alembic -c migrations/api-server/alembic.ini current
```

`downgrade` 会删除表和数据，只能在明确确认本地数据可删除后执行；当前不提供生产环境回滚命令。
