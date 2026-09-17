# 本文件定义 PostgreSQL P0 持久化模型。
# 定义 User、Conversation、Message、Run、AuditEvent、Trip 与 TravelPolicyRule，
# 覆盖用户、会话、运行、审计、行程和政策事实。
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Column, DateTime, Index, LargeBinary, Numeric, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    """合并登录账号和用户档案；高敏感资料只保存 AES-GCM 密文及 nonce。"""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        Index("ix_users_role", "role"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, max_length=64)
    username: str = Field(max_length=64)
    password_hash: str = Field(max_length=256)
    role: str = Field(default="user", max_length=16, index=True)
    base_city: str | None = Field(default=None, max_length=128)
    level: str | None = Field(default=None, max_length=16)
    gender: str | None = Field(default=None, max_length=4)
    sensitive_ciphertext: bytes | None = Field(
        default=None, sa_column=Column("sensitive_ciphertext", LargeBinary, nullable=True)
    )
    sensitive_nonce: bytes | None = Field(
        default=None, sa_column=Column("sensitive_nonce", LargeBinary, nullable=True)
    )
    sensitive_key_version: int | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
        ),
    )
    disabled_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class Conversation(SQLModel, table=True):
    """保存用户长期会话容器和逻辑删除状态。"""

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)

    conversation_id: str = Field(primary_key=True, max_length=64)
    user_id: str = Field(foreign_key="users.user_id", index=True, max_length=64)
    title: str = Field(default="新对话", max_length=256)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
        ),
    )
    deleted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class Message(SQLModel, table=True):
    """保存用户和助手可见消息；正文采用 AES-GCM 密文，禁止明文落库。"""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    message_id: str = Field(primary_key=True, max_length=64)
    conversation_id: str = Field(
        foreign_key="conversations.conversation_id", index=True, max_length=64
    )
    run_id: str | None = Field(default=None, foreign_key="runs.run_id", index=True, max_length=64)
    role: str = Field(max_length=32)
    agent_name: str | None = Field(default=None, max_length=128)
    content_ciphertext: bytes | None = Field(
        default=None, sa_column=Column("content_ciphertext", LargeBinary, nullable=True)
    )
    content_nonce: bytes | None = Field(
        default=None, sa_column=Column("content_nonce", LargeBinary, nullable=True)
    )
    content_key_version: int | None = Field(default=None)
    extra: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    feedback: str | None = Field(default=None, max_length=16)
    feedback_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class Run(SQLModel, table=True):
    """保存一次可恢复 Agent 运行及四类关联标识。"""

    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_user_created", "user_id", "created_at"),
        Index("ix_runs_thread", "thread_id"),
        Index("ix_runs_status", "status"),
    )

    run_id: str = Field(primary_key=True, max_length=64)
    conversation_id: str = Field(
        foreign_key="conversations.conversation_id", index=True, max_length=64
    )
    user_id: str = Field(foreign_key="users.user_id", index=True, max_length=64)
    thread_id: str = Field(max_length=128)
    status: str = Field(default="created", max_length=32)
    intent_summary: str | None = Field(default=None, max_length=2000)
    trace_id: str = Field(max_length=128)
    request_id: str = Field(max_length=128)
    error_code: str | None = Field(default=None, max_length=128)
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    ended_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
        ),
    )


class AuditEvent(SQLModel, table=True):
    """保存脱敏业务审计事实，不保存完整请求正文或私有思维链。"""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_trace_created", "trace_id", "created_at"),
        Index("ix_audit_events_user_created", "user_id", "created_at"),
    )

    audit_id: str = Field(primary_key=True, max_length=64)
    event_type: str = Field(max_length=128)
    outcome: str = Field(max_length=32)
    user_id: str | None = Field(
        default=None, foreign_key="users.user_id", index=True, max_length=64
    )
    trace_id: str = Field(max_length=128)
    request_id: str = Field(max_length=128)
    run_id: str | None = Field(default=None, index=True, max_length=64)
    thread_id: str | None = Field(default=None, max_length=128)
    summary: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class Trip(SQLModel, table=True):
    """保存用户确认的行程事实或只读候选摘要，不代表真实订单。"""

    __tablename__ = "trips"
    __table_args__ = (Index("ix_trips_user_start", "user_id", "start_date"),)

    trip_id: str = Field(primary_key=True, max_length=64)
    user_id: str = Field(foreign_key="users.user_id", index=True, max_length=64)
    conversation_id: str | None = Field(
        default=None, foreign_key="conversations.conversation_id", index=True, max_length=64
    )
    title: str = Field(max_length=256)
    origin: str | None = Field(default=None, max_length=128)
    destination: str | None = Field(default=None, max_length=128)
    start_date: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    end_date: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    status: str = Field(default="candidate", max_length=32)
    source: str | None = Field(default=None, max_length=256)
    source_version: str | None = Field(default=None, max_length=128)
    queried_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    details: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class TravelOrder(SQLModel, table=True):
    """保存用户差旅意图、日期、事由、审批关联和生命周期状态。"""

    __tablename__ = "travel_order"
    __table_args__ = (
        Index("ix_travel_order_user_id", "user_id"),
        Index("ix_travel_order_user_status", "user_id", "status"),
    )

    order_id: str = Field(primary_key=True, max_length=64)
    user_id: str = Field(foreign_key="users.user_id", max_length=64)
    destination: str | None = Field(default=None, max_length=256)
    departure_city: str | None = Field(default=None, max_length=128)
    departure_date: date | None = Field(default=None)
    return_date: date | None = Field(default=None)
    purpose: str | None = Field(default=None, max_length=512)
    status: str = Field(default="DRAFT", max_length=32)
    approval_id: str | None = Field(default=None, max_length=64)
    plan_html_url: str | None = Field(default=None, max_length=512)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
        ),
    )


class ApprovalRecord(SQLModel, table=True):
    """保存差旅审批流程实例、状态流转和提交时表单快照。"""

    __tablename__ = "approval_record"
    __table_args__ = (
        Index("ix_approval_record_user_id", "user_id"),
        Index("ix_approval_record_order_id", "order_id"),
        Index("ix_approval_record_order_submit", "order_id", "submit_time"),
    )

    process_instance_id: str = Field(primary_key=True, max_length=64)
    user_id: str = Field(foreign_key="users.user_id", max_length=64)
    title: str | None = Field(default=None, max_length=256)
    status: str = Field(default="PENDING", max_length=32)
    approval_form: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )
    remark: str | None = Field(default=None, max_length=512)
    submit_time: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    update_time: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    order_id: str | None = Field(default=None, max_length=64)


class UserApiKey(SQLModel, table=True):
    """保存用户级第三方 API Key 的 AES-GCM 密文，业务键为 (user_id, provider)。"""

    __tablename__ = "user_api_key"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_api_key_user_provider"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(foreign_key="users.user_id", index=True, max_length=64)
    provider: str = Field(max_length=64)
    api_key_ciphertext: bytes = Field(
        sa_column=Column("api_key_ciphertext", LargeBinary, nullable=False)
    )
    api_key_nonce: bytes = Field(
        sa_column=Column("api_key_nonce", LargeBinary, nullable=False)
    )
    api_key_key_version: int = Field(default=1)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
        ),
    )


class BookingRecord(SQLModel, table=True):
    """保存差旅关联的机票、酒店和火车票预订记录及内部状态。"""

    __tablename__ = "booking_record"
    __table_args__ = (
        UniqueConstraint("booking_id", name="uq_booking_record_booking_id"),
        UniqueConstraint(
            "platform", "biz_type", "external_order_no", name="uq_booking_platform_order"
        ),
        Index("ix_booking_record_user_id", "user_id"),
        Index("ix_booking_record_user_biz", "user_id", "biz_type"),
        Index("ix_booking_record_travel_order_id", "travel_order_id"),
        Index("ix_booking_record_status", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    booking_id: str = Field(max_length=64)
    user_id: str = Field(foreign_key="users.user_id", max_length=64)
    conversation_id: str | None = Field(default=None, max_length=64)
    travel_order_id: str | None = Field(default=None, max_length=64)
    biz_type: str = Field(max_length=16)
    platform: str = Field(max_length=32)
    external_order_no: str | None = Field(default=None, max_length=128)
    status: str = Field(default="CREATED", max_length=32)
    external_status: str | None = Field(default=None, max_length=64)
    payment_status: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=256)
    total_amount: Decimal | None = Field(default=None, sa_column=Column(Numeric(12, 2)))
    currency: str = Field(default="CNY", max_length=8)
    contact_name: str | None = Field(default=None, max_length=64)
    contact_phone: str | None = Field(default=None, max_length=32)
    start_time: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    end_time: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    detail: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    remark: str | None = Field(default=None, max_length=512)
    booked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
        ),
    )
    deleted: bool = Field(default=False)
class TravelPolicyRule(SQLModel, table=True):
    """保存按职级和城市等级匹配的企业差旅政策规则。"""

    __tablename__ = "travel_policy_rules"
    __table_args__ = (
        UniqueConstraint("level_min", "level_max", "city_tier", name="uq_policy_level_city"),
    )

    id: int | None = Field(default=None, primary_key=True)
    level_min: int = Field()
    level_max: int = Field()
    city_tier: str = Field(max_length=16)
    flight_class: str | None = Field(default=None, max_length=32)
    train_seat_class: str | None = Field(default=None, max_length=16)
    hotel_limit: Decimal = Field(
        default=Decimal("0"), sa_column=Column(Numeric(12, 2), nullable=False)
    )
    hotel_star_limit: int = Field(default=4)
    daily_meal_limit: Decimal = Field(
        default=Decimal("0"), sa_column=Column(Numeric(12, 2), nullable=False)
    )
    daily_transport_limit: Decimal = Field(
        default=Decimal("0"), sa_column=Column(Numeric(12, 2), nullable=False)
    )
    approval_threshold: Decimal = Field(
        default=Decimal("0"), sa_column=Column(Numeric(12, 2), nullable=False)
    )
    advance_booking_days: int = Field(default=3)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
        ),
    )
