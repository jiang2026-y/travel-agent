# 本文件创建 ItineraryManageAgent 所需的差旅申请、审批和预订 PostgreSQL 表。
# 定义 upgrade 创建三张业务表与索引，downgrade 按依赖顺序回滚这些表。
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260904_0002"
down_revision = "20260819_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建差旅申请单、审批单和预订记录表，状态值由应用层状态机约束。"""
    op.create_table(
        "travel_order",
        sa.Column("order_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("destination", sa.String(256)),
        sa.Column("departure_city", sa.String(128)),
        sa.Column("departure_date", sa.Date()),
        sa.Column("return_date", sa.Date()),
        sa.Column("purpose", sa.String(512)),
        sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("approval_id", sa.String(64)),
        sa.Column("plan_html_url", sa.String(512)),
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
        sa.CheckConstraint(
            "status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','COMPLETED','CANCELLED')",
            name="ck_travel_order_status",
        ),
        sa.CheckConstraint(
            "return_date IS NULL OR departure_date IS NULL OR return_date >= departure_date",
            name="ck_travel_order_date_range",
        ),
    )
    op.create_index("ix_travel_order_user_id", "travel_order", ["user_id"])
    op.create_index("ix_travel_order_user_status", "travel_order", ["user_id", "status"])

    op.create_table(
        "approval_record",
        sa.Column("process_instance_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("title", sa.String(256)),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("approval_form", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("remark", sa.String(512)),
        sa.Column("submit_time", sa.DateTime(timezone=True)),
        sa.Column("update_time", sa.DateTime(timezone=True)),
        sa.Column("order_id", sa.String(64)),
        sa.CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED','CANCELLED')",
            name="ck_approval_record_status",
        ),
    )
    op.create_index("ix_approval_record_user_id", "approval_record", ["user_id"])
    op.create_index("ix_approval_record_order_id", "approval_record", ["order_id"])
    op.create_index(
        "ix_approval_record_order_submit", "approval_record", ["order_id", "submit_time"]
    )

    op.create_table(
        "booking_record",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("booking_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("conversation_id", sa.String(64)),
        sa.Column("travel_order_id", sa.String(64)),
        sa.Column("biz_type", sa.String(16), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("external_order_no", sa.String(128)),
        sa.Column("status", sa.String(32), nullable=False, server_default="CREATED"),
        sa.Column("external_status", sa.String(64)),
        sa.Column("payment_status", sa.String(32)),
        sa.Column("title", sa.String(256)),
        sa.Column("total_amount", sa.Numeric(12, 2)),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("contact_name", sa.String(64)),
        sa.Column("contact_phone", sa.String(32)),
        sa.Column("start_time", sa.DateTime(timezone=True)),
        sa.Column("end_time", sa.DateTime(timezone=True)),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("remark", sa.String(512)),
        sa.Column("booked_at", sa.DateTime(timezone=True)),
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
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "status IN ('CREATED','PENDING_PAYMENT','PAID','CONFIRMED','COMPLETED',"
            "'CANCELLED','REFUNDED','FAILED')",
            name="ck_booking_record_status",
        ),
        sa.UniqueConstraint("booking_id", name="uq_booking_record_booking_id"),
        sa.UniqueConstraint(
            "platform", "biz_type", "external_order_no", name="uq_booking_platform_order"
        ),
    )
    op.create_index("ix_booking_record_user_id", "booking_record", ["user_id"])
    op.create_index("ix_booking_record_user_biz", "booking_record", ["user_id", "biz_type"])
    op.create_index("ix_booking_record_travel_order_id", "booking_record", ["travel_order_id"])
    op.create_index("ix_booking_record_status", "booking_record", ["status"])


def downgrade() -> None:
    """按预订、审批和差旅申请的依赖顺序删除三张业务表。"""
    op.drop_index("ix_booking_record_status", table_name="booking_record")
    op.drop_index("ix_booking_record_travel_order_id", table_name="booking_record")
    op.drop_index("ix_booking_record_user_biz", table_name="booking_record")
    op.drop_index("ix_booking_record_user_id", table_name="booking_record")
    op.drop_table("booking_record")
    op.drop_index("ix_approval_record_order_submit", table_name="approval_record")
    op.drop_index("ix_approval_record_order_id", table_name="approval_record")
    op.drop_index("ix_approval_record_user_id", table_name="approval_record")
    op.drop_table("approval_record")
    op.drop_index("ix_travel_order_user_status", table_name="travel_order")
    op.drop_index("ix_travel_order_user_id", table_name="travel_order")
    op.drop_table("travel_order")
