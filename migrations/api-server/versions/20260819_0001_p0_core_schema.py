# 本文件创建 P0 PostgreSQL 核心 Schema。
# 定义 upgrade 创建用户、会话、消息、Run、审计、行程和政策表；定义 downgrade 按依赖反向删除。
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260819_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建经确认的 P0 表与索引；不创建订单、预订、审批或用户 API Key 表。"""
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="user"),
        sa.Column("base_city", sa.String(length=128), nullable=True),
        sa.Column("level", sa.String(length=16), nullable=True),
        sa.Column("gender", sa.String(length=4), nullable=True),
        sa.Column("sensitive_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("sensitive_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("sensitive_key_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
        sa.CheckConstraint(
            "(sensitive_ciphertext IS NULL AND sensitive_nonce IS NULL "
            "AND sensitive_key_version IS NULL) "
            "OR (sensitive_ciphertext IS NOT NULL AND sensitive_nonce IS NOT NULL "
            "AND sensitive_key_version IS NOT NULL)",
            name="ck_users_sensitive_payload_complete",
        ),
        sa.UniqueConstraint("user_id", name="uq_users_user_id"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_role", "users", ["role"])

    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False, server_default="新对话"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_index("ix_conversations_user_updated", "conversations", ["user_id", "updated_at"])

    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            sa.ForeignKey("conversations.conversation_id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="created"),
        sa.Column("intent_summary", sa.String(length=2000), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_runs_conversation_id", "runs", ["conversation_id"])
    op.create_index("ix_runs_user_id", "runs", ["user_id"])
    op.create_index("ix_runs_user_created", "runs", ["user_id", "created_at"])
    op.create_index("ix_runs_thread", "runs", ["thread_id"])
    op.create_index("ix_runs_status", "runs", ["status"])

    op.create_table(
        "messages",
        sa.Column("message_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            sa.ForeignKey("conversations.conversation_id"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("runs.run_id"), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("agent_name", sa.String(length=128), nullable=True),
        sa.Column("content_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("content_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("content_key_version", sa.Integer(), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("feedback", sa.String(length=16), nullable=True),
        sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "(content_ciphertext IS NULL AND content_nonce IS NULL "
            "AND content_key_version IS NULL) "
            "OR (content_ciphertext IS NOT NULL AND content_nonce IS NOT NULL "
            "AND content_key_version IS NOT NULL)",
            name="ck_messages_content_payload_complete",
        ),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_run_id", "messages", ["run_id"])
    op.create_index(
        "ix_messages_conversation_created", "messages", ["conversation_id", "created_at"]
    )

    op.create_table(
        "audit_events",
        sa.Column("audit_id", sa.String(length=64), primary_key=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("thread_id", sa.String(length=128), nullable=True),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"])
    op.create_index("ix_audit_events_run_id", "audit_events", ["run_id"])
    op.create_index("ix_audit_events_trace_created", "audit_events", ["trace_id", "created_at"])
    op.create_index("ix_audit_events_user_created", "audit_events", ["user_id", "created_at"])

    op.create_table(
        "trips",
        sa.Column("trip_id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            sa.ForeignKey("conversations.conversation_id"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("origin", sa.String(length=128), nullable=True),
        sa.Column("destination", sa.String(length=128), nullable=True),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="candidate"),
        sa.Column("source", sa.String(length=256), nullable=True),
        sa.Column("source_version", sa.String(length=128), nullable=True),
        sa.Column("queried_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_trips_user_id", "trips", ["user_id"])
    op.create_index("ix_trips_conversation_id", "trips", ["conversation_id"])
    op.create_index("ix_trips_user_start", "trips", ["user_id", "start_date"])

    op.create_table(
        "travel_policy_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("level_min", sa.Integer(), nullable=False),
        sa.Column("level_max", sa.Integer(), nullable=False),
        sa.Column("city_tier", sa.String(length=16), nullable=False),
        sa.Column("flight_class", sa.String(length=32), nullable=True),
        sa.Column("train_seat_class", sa.String(length=16), nullable=True),
        sa.Column("hotel_limit", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("hotel_star_limit", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("daily_meal_limit", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("daily_transport_limit", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("approval_threshold", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("advance_booking_days", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("level_min <= level_max", name="ck_policy_level_range"),
        sa.UniqueConstraint("level_min", "level_max", "city_tier", name="uq_policy_level_city"),
    )


def downgrade() -> None:
    """按外键依赖反向删除 P0 表；仅在明确确认本地数据可删除后执行。"""
    op.drop_table("travel_policy_rules")
    op.drop_index("ix_trips_user_start", table_name="trips")
    op.drop_index("ix_trips_conversation_id", table_name="trips")
    op.drop_index("ix_trips_user_id", table_name="trips")
    op.drop_table("trips")
    op.drop_index("ix_audit_events_user_created", table_name="audit_events")
    op.drop_index("ix_audit_events_trace_created", table_name="audit_events")
    op.drop_index("ix_audit_events_run_id", table_name="audit_events")
    op.drop_index("ix_audit_events_user_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_messages_conversation_created", table_name="messages")
    op.drop_index("ix_messages_run_id", table_name="messages")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_index("ix_runs_thread", table_name="runs")
    op.drop_index("ix_runs_user_created", table_name="runs")
    op.drop_index("ix_runs_user_id", table_name="runs")
    op.drop_index("ix_runs_conversation_id", table_name="runs")
    op.drop_table("runs")
    op.drop_index("ix_conversations_user_updated", table_name="conversations")
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_table("users")
