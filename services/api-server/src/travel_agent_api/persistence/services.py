# 本文件实现 PostgreSQL 用户目录与审计事实服务。
# 定义 CredentialUser、DatabaseUserDirectory 与 PostgresAuditService。
# 它们分别提供认证凭据视图、用户查询和脱敏审计读写。
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from travel_agent_sensitive_masker import SensitiveMasker

from travel_agent_api.application.audit_service import AuditEntry
from travel_agent_api.core.correlation import CorrelationContext
from travel_agent_api.core.encryption import DataEncryptionService, EncryptedPayload
from travel_agent_api.persistence.database import metadata
from travel_agent_api.persistence.models import AuditEvent, Conversation, Message, Run, User


@dataclass(frozen=True, slots=True)
class CredentialUser:
    """表示认证所需的最小用户信息，不包含敏感档案密文。"""

    user_id: str
    account: str
    role: str
    password_hash: str


@dataclass(slots=True)
class DatabaseUserDirectory:
    """从 PostgreSQL 查询可登录用户，拒绝被停用的账号。"""

    session_factory: async_sessionmaker[AsyncSession]

    async def find_by_account(self, account: str) -> CredentialUser | None:
        """按登录账号查询仍可用的用户及其密码哈希。"""
        users = metadata.tables["users"].c
        statement = select(User).where(users.username == account, users.disabled_at.is_(None))
        async with self.session_factory() as session:
            user = await session.scalar(statement)
        return _credential_user(user)

    async def find_by_user_id(self, user_id: str) -> CredentialUser | None:
        """按会话关联的 user_id 查询仍可用的用户。"""
        users = metadata.tables["users"].c
        statement = select(User).where(users.user_id == user_id, users.disabled_at.is_(None))
        async with self.session_factory() as session:
            user = await session.scalar(statement)
        return _credential_user(user)


@dataclass(slots=True)
class PostgresAuditService:
    """把脱敏审计事实写入 PostgreSQL，并按时间倒序供管理员查询。"""

    session_factory: async_sessionmaker[AsyncSession]

    async def record(
        self,
        event_type: str,
        actor_user_id: str | None,
        outcome: str,
        trace_id: str,
        request_id: str,
        run_id: str | None = None,
        thread_id: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        """创建不含原始请求正文、密码或凭据的审计事实。"""
        event = AuditEvent(
            audit_id=f"audit_{uuid.uuid4().hex}",
            event_type=event_type,
            outcome=outcome,
            user_id=actor_user_id,
            trace_id=trace_id,
            request_id=request_id,
            run_id=run_id,
            thread_id=thread_id,
            summary=summary,
        )
        async with self.session_factory() as session:
            session.add(event)
            await session.commit()

    async def list_entries(self, limit: int = 100) -> tuple[AuditEntry, ...]:
        """读取受限数量的审计元数据快照，不解密或返回敏感业务正文。"""
        audit_events = metadata.tables["audit_events"].c
        statement = select(AuditEvent).order_by(desc(audit_events.created_at)).limit(limit)
        async with self.session_factory() as session:
            events = (await session.scalars(statement)).all()
        return tuple(
            AuditEntry(
                event_type=event.event_type,
                actor_user_id=event.user_id,
                outcome=event.outcome,
                trace_id=event.trace_id,
                request_id=event.request_id,
                run_id=event.run_id,
                thread_id=event.thread_id,
                created_at=event.created_at or datetime.now(UTC),
            )
            for event in events
        )


def _credential_user(user: User | None) -> CredentialUser | None:
    """将 ORM 用户转换为认证层所需的最小只读凭据视图。"""
    if user is None:
        return None
    return CredentialUser(
        user_id=user.user_id,
        account=user.username,
        role=user.role,
        password_hash=user.password_hash,
    )


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    """表示会话列表可返回的非敏感摘要。"""

    conversation_id: str
    title: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RunSummary:
    """表示当前用户可读取的 Run 状态元数据。"""

    run_id: str
    conversation_id: str
    thread_id: str
    status: str


@dataclass(slots=True)
class PostgresConversationService:
    """原子创建会话、加密消息和 Run，并提供按所有者隔离的查询。"""

    session_factory: async_sessionmaker[AsyncSession]
    encryption: DataEncryptionService

    async def get_desensitized_context_summary(
        self, user_id: str, conversation_id: str
    ) -> str:
        """提取当前会话最近十条历史消息并脱敏，截断至 2000 字符后供 Agent 使用。"""
        conversations = metadata.tables["conversations"].c
        messages = metadata.tables["messages"].c
        ownership = select(Conversation.conversation_id).where(
            conversations.conversation_id == conversation_id,
            conversations.user_id == user_id,
            conversations.deleted_at.is_(None),
        )
        statement = (
            select(Message)
            .where(messages.conversation_id.in_(ownership))
            .order_by(desc(messages.created_at))
            .limit(10)
        )
        async with self.session_factory() as session:
            rows = list((await session.scalars(statement)).all())
        masker = SensitiveMasker()
        summaries: list[str] = []
        for row in reversed(rows):
            if (
                row.content_ciphertext is None
                or row.content_nonce is None
                or row.content_key_version is None
            ):
                continue
            try:
                content = self.encryption.decrypt_json(
                    EncryptedPayload(
                        row.content_ciphertext, row.content_nonce, row.content_key_version
                    )
                ).get("content")
            except Exception:
                continue
            if isinstance(content, str) and content.strip():
                summaries.append(f"{row.role}: {masker.mask_text(content.strip())}")
        return "\n".join(summaries)[-2000:]

    async def start_run(
        self, user_id: str, message: str, correlation: CorrelationContext
    ) -> RunSummary:
        """创建新会话和初始用户消息；此阶段只入队，不调用 Agent。"""
        conversation_id = f"conv_{uuid.uuid4().hex}"
        run_id = f"run_{uuid.uuid4().hex}"
        thread_id = f"thread_{uuid.uuid4().hex}"
        encrypted = self.encryption.encrypt_json({"content": message})
        conversation = Conversation(conversation_id=conversation_id, user_id=user_id)
        run = Run(
            run_id=run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            thread_id=thread_id,
            status="queued",
            trace_id=correlation.trace_id,
            request_id=correlation.request_id,
        )
        stored_message = Message(
            message_id=f"msg_{uuid.uuid4().hex}",
            conversation_id=conversation_id,
            run_id=run_id,
            role="user",
            content_ciphertext=encrypted.ciphertext,
            content_nonce=encrypted.nonce,
            content_key_version=encrypted.key_version,
        )
        async with self.session_factory() as session:
            session.add(conversation)
            await session.flush()
            session.add(run)
            await session.flush()
            session.add(stored_message)
            await session.commit()
        return RunSummary(run_id, conversation_id, thread_id, "queued")

    async def list_conversations(self, user_id: str) -> tuple[ConversationSummary, ...]:
        """按最近活动时间读取当前用户未删除的会话摘要。"""
        conversations = metadata.tables["conversations"].c
        statement = (
            select(Conversation)
            .where(conversations.user_id == user_id, conversations.deleted_at.is_(None))
            .order_by(desc(conversations.updated_at))
            .limit(100)
        )
        async with self.session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(
            ConversationSummary(row.conversation_id, row.title, row.updated_at) for row in rows
        )

    async def get_run(self, user_id: str, run_id: str) -> RunSummary | None:
        """仅返回属于当前用户的 Run，避免跨用户枚举。"""
        runs = metadata.tables["runs"].c
        statement = select(Run).where(runs.run_id == run_id, runs.user_id == user_id)
        async with self.session_factory() as session:
            run = await session.scalar(statement)
        if run is None:
            return None
        return RunSummary(run.run_id, run.conversation_id, run.thread_id, run.status)

    async def append_user_message(
        self, user_id: str, run_id: str, message: str
    ) -> RunSummary | None:
        """为当前用户拥有的 Run 加密保存补充消息，供恢复命令在后续编排阶段读取。"""
        runs = metadata.tables["runs"].c
        statement = select(Run).where(runs.run_id == run_id, runs.user_id == user_id)
        async with self.session_factory() as session:
            run = await session.scalar(statement)
            if run is None:
                return None
            encrypted = self.encryption.encrypt_json({"content": message})
            session.add(
                Message(
                    message_id=f"msg_{uuid.uuid4().hex}",
                    conversation_id=run.conversation_id,
                    run_id=run.run_id,
                    role="user",
                    content_ciphertext=encrypted.ciphertext,
                    content_nonce=encrypted.nonce,
                    content_key_version=encrypted.key_version,
                )
            )
            await session.commit()
        return RunSummary(run.run_id, run.conversation_id, run.thread_id, run.status)

    async def update_run_status(
        self, user_id: str, run_id: str, status: str
    ) -> RunSummary | None:
        """仅更新当前用户 Run 的公开生命周期状态，不写入模型输出或工具调用细节。"""
        runs = metadata.tables["runs"].c
        statement = select(Run).where(runs.run_id == run_id, runs.user_id == user_id)
        async with self.session_factory() as session:
            run = await session.scalar(statement)
            if run is None:
                return None
            run.status = status
            if status == "running" and run.started_at is None:
                run.started_at = datetime.now(UTC)
            if status in {"cancelled", "completed", "failed"}:
                run.ended_at = datetime.now(UTC)
            await session.commit()
        return RunSummary(run.run_id, run.conversation_id, run.thread_id, run.status)
