# 本文件实现 PostgreSQL 用户目录与审计事实服务。
# 定义 CredentialUser、DatabaseUserDirectory 与 PostgresAuditService。
# 它们分别提供认证凭据视图、用户查询和脱敏审计读写。
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from travel_agent_sensitive_masker import SensitiveMasker

from travel_agent_api.application.audit_service import AuditEntry
from travel_agent_api.core.correlation import CorrelationContext
from travel_agent_api.core.encryption import DataEncryptionService, EncryptedPayload
from travel_agent_api.persistence.database import metadata
from travel_agent_api.persistence.models import (
    AuditEvent,
    Conversation,
    Message,
    Run,
    User,
    UserApiKey,
)


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


async def _touch_conversation(session: AsyncSession, conversation_id: str) -> None:
    """刷新会话的更新时间，使会话列表按最近活动正确排序。"""
    conversations = metadata.tables["conversations"].c
    await session.execute(
        update(Conversation)
        .where(conversations.conversation_id == conversation_id)
        .values(updated_at=datetime.now(UTC))
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


@dataclass(frozen=True, slots=True)
class MessageSummary:
    """表示可安全返回前端的已脱敏会话消息。"""

    message_id: str
    run_id: str | None
    role: str
    content: str
    created_at: datetime
    feedback: str | None = None


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
        ownership = select(conversations.conversation_id).where(
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

    async def start_turn(
        self, user_id: str, conversation_id: str, message: str, correlation: CorrelationContext
    ) -> RunSummary | None:
        """在既有会话内开启新一轮：新建 Run 并复用该会话的 thread_id 以保留上下文。"""
        conversations = metadata.tables["conversations"].c
        runs = metadata.tables["runs"].c
        run_id = f"run_{uuid.uuid4().hex}"
        encrypted = self.encryption.encrypt_json({"content": message})
        async with self.session_factory() as session:
            conversation = await session.scalar(
                select(Conversation).where(
                    conversations.conversation_id == conversation_id,
                    conversations.user_id == user_id,
                    conversations.deleted_at.is_(None),
                )
            )
            if conversation is None:
                return None
            # 复用该会话最近一次 Run 的 thread_id，使主智能体与子 Agent 的检查点延续。
            previous_thread = await session.scalar(
                select(runs.thread_id)
                .where(runs.conversation_id == conversation_id)
                .order_by(desc(runs.created_at))
                .limit(1)
            )
            thread_id = (
                previous_thread
                if isinstance(previous_thread, str) and previous_thread
                else f"thread_{uuid.uuid4().hex}"
            )
            session.add(
                Run(
                    run_id=run_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    thread_id=thread_id,
                    status="queued",
                    trace_id=correlation.trace_id,
                    request_id=correlation.request_id,
                )
            )
            await session.flush()
            session.add(
                Message(
                    message_id=f"msg_{uuid.uuid4().hex}",
                    conversation_id=conversation_id,
                    run_id=run_id,
                    role="user",
                    content_ciphertext=encrypted.ciphertext,
                    content_nonce=encrypted.nonce,
                    content_key_version=encrypted.key_version,
                )
            )
            conversation.updated_at = datetime.now(UTC)
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
            # 追加消息即视为会话有新活动，刷新更新时间以保持会话列表排序正确。
            await _touch_conversation(session, run.conversation_id)
            await session.commit()
        return RunSummary(run.run_id, run.conversation_id, run.thread_id, run.status)

    async def append_assistant_message(
        self, user_id: str, run_id: str, content: str
    ) -> MessageSummary | None:
        """加密保存 Agent 主回复，并验证 Run 所属用户。"""
        runs = metadata.tables["runs"].c
        statement = select(Run).where(runs.run_id == run_id, runs.user_id == user_id)
        encrypted = self.encryption.encrypt_json({"content": content})
        async with self.session_factory() as session:
            run = await session.scalar(statement)
            if run is None:
                return None
            item = Message(
                message_id=f"msg_{uuid.uuid4().hex}",
                conversation_id=run.conversation_id,
                run_id=run.run_id,
                role="assistant",
                content_ciphertext=encrypted.ciphertext,
                content_nonce=encrypted.nonce,
                content_key_version=encrypted.key_version,
            )
            session.add(item)
            await _touch_conversation(session, run.conversation_id)
            await session.commit()
        return MessageSummary(item.message_id, item.run_id, item.role, content, item.created_at)

    async def list_messages(
        self, user_id: str, conversation_id: str
    ) -> tuple[MessageSummary, ...]:
        """按会话归属读取并解密可见消息，返回脱敏后的文本。"""
        conversations = metadata.tables["conversations"].c
        messages = metadata.tables["messages"].c
        ownership = select(conversations.conversation_id).where(
            conversations.conversation_id == conversation_id,
            conversations.user_id == user_id,
            conversations.deleted_at.is_(None),
        )
        statement = (
            select(Message)
            .where(messages.conversation_id.in_(ownership))
            .order_by(messages.created_at)
        )
        masker = SensitiveMasker()
        summaries: list[MessageSummary] = []
        async with self.session_factory() as session:
            rows = (await session.scalars(statement)).all()
        for row in rows:
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
                summaries.append(
                    MessageSummary(
                        row.message_id,
                        row.run_id,
                        row.role,
                        masker.mask_text(content.strip()),
                        row.created_at or datetime.now(UTC),
                        row.feedback,
                    )
                )
        return tuple(summaries)

    async def set_message_feedback(
        self,
        user_id: str,
        conversation_id: str,
        message_id: str,
        feedback: str | None,
    ) -> str | None:
        """写入或清除助手消息反馈；只允许 up/down/null，越权返回 None。"""
        if feedback not in {None, "up", "down"}:
            raise ValueError("message_feedback_invalid")
        conversations = metadata.tables["conversations"].c
        messages = metadata.tables["messages"].c
        ownership = select(conversations.conversation_id).where(
            conversations.conversation_id == conversation_id,
            conversations.user_id == user_id,
            conversations.deleted_at.is_(None),
        )
        async with self.session_factory() as session:
            row = await session.scalar(
                select(Message).where(
                    messages.message_id == message_id,
                    messages.conversation_id.in_(ownership),
                    messages.role == "assistant",
                )
            )
            if not isinstance(row, Message):
                return None
            row.feedback = feedback
            row.feedback_at = None if feedback is None else datetime.now(UTC)
            await session.commit()
        return feedback

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

    async def update_title(
        self, user_id: str, conversation_id: str, title: str
    ) -> str | None:
        """更新当前用户会话标题；标题未变化或会话不可见时返回 None。"""
        conversations = metadata.tables["conversations"].c
        statement = select(Conversation).where(
            conversations.conversation_id == conversation_id,
            conversations.user_id == user_id,
            conversations.deleted_at.is_(None),
        )
        async with self.session_factory() as session:
            conversation = await session.scalar(statement)
            if conversation is None:
                return None
            normalized = title.strip()[:256]
            if not normalized or conversation.title == normalized:
                return None
            conversation.title = normalized
            await session.commit()
        return normalized

    async def rename_conversation(
        self, user_id: str, conversation_id: str, title: str
    ) -> str | None:
        """按用户主动改名校验后写入标题；会话不可见时返回 None。"""
        normalized = title.strip()
        if not normalized or len(normalized) > 64:
            raise ValueError("conversation_title_invalid")
        conversations = metadata.tables["conversations"].c
        statement = select(Conversation).where(
            conversations.conversation_id == conversation_id,
            conversations.user_id == user_id,
            conversations.deleted_at.is_(None),
        )
        async with self.session_factory() as session:
            conversation = await session.scalar(statement)
            if conversation is None:
                return None
            conversation.title = normalized
            conversation.updated_at = datetime.now(UTC)
            await session.commit()
        return normalized

    async def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        """软删除当前用户会话；不可见或已删除时返回 False。"""
        conversations = metadata.tables["conversations"].c
        async with self.session_factory() as session:
            result = await session.execute(
                update(Conversation)
                .where(
                    conversations.conversation_id == conversation_id,
                    conversations.user_id == user_id,
                    conversations.deleted_at.is_(None),
                )
                .values(deleted_at=datetime.now(UTC), updated_at=datetime.now(UTC))
            )
            await session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def fail_stale_runs(
        self, *, older_than_hours: int = 24
    ) -> tuple[tuple[str, str, str], ...]:
        """把长期未结束的运行标记为 failed，返回 (user_id, run_id, thread_id) 列表。"""
        if older_than_hours <= 0:
            raise ValueError("stale_run_hours_must_be_positive")
        runs = metadata.tables["runs"].c
        deadline = datetime.now(UTC) - timedelta(hours=older_than_hours)
        statement = select(Run).where(
            runs.status.in_(("created", "queued", "running")),
            runs.created_at < deadline,
        )
        async with self.session_factory() as session:
            rows = list((await session.scalars(statement)).all())
            if not rows:
                return ()
            ended_at = datetime.now(UTC)
            affected: list[tuple[str, str, str]] = []
            for row in rows:
                row.status = "failed"
                row.ended_at = ended_at
                affected.append((row.user_id, row.run_id, row.thread_id))
            await session.commit()
        return tuple(affected)


@dataclass(slots=True)
class PostgresUserApiKeyService:
    """按用户与 provider 读写第三方 API Key 密文，永不回显明文。"""

    session_factory: async_sessionmaker[AsyncSession]
    encryption: DataEncryptionService

    async def has_key(self, user_id: str, provider: str) -> bool:
        """判断当前用户是否已配置指定 provider 的 API Key。"""
        return await self._load(user_id, provider) is not None

    async def reveal(self, user_id: str, provider: str) -> str | None:
        """解密返回明文 API Key，仅供受保护内部接口在写操作前注入使用。"""
        row = await self._load(user_id, provider)
        if row is None:
            return None
        if (
            row.api_key_ciphertext is None
            or row.api_key_nonce is None
            or row.api_key_key_version is None
        ):
            return None
        try:
            payload = self.encryption.decrypt_json(
                EncryptedPayload(
                    row.api_key_ciphertext, row.api_key_nonce, row.api_key_key_version
                )
            )
        except Exception:
            return None
        api_key = payload.get("api_key")
        return api_key if isinstance(api_key, str) and api_key else None

    async def save(self, user_id: str, provider: str, api_key: str) -> None:
        """以 AES-GCM 密文 upsert 用户 API Key，明文不落库、不写日志。"""
        encrypted = self.encryption.encrypt_json({"api_key": api_key})
        api_keys = metadata.tables["user_api_key"].c
        async with self.session_factory() as session:
            row = await session.scalar(
                select(UserApiKey).where(
                    api_keys.user_id == user_id, api_keys.provider == provider
                )
            )
            if row is None:
                row = UserApiKey(user_id=user_id, provider=provider)
            row.api_key_ciphertext = encrypted.ciphertext
            row.api_key_nonce = encrypted.nonce
            row.api_key_key_version = encrypted.key_version
            session.add(row)
            await session.commit()

    async def delete(self, user_id: str, provider: str) -> None:
        """删除当前用户的 API Key 记录，幂等且不抛错。"""
        api_keys = metadata.tables["user_api_key"].c
        async with self.session_factory() as session:
            row = await session.scalar(
                select(UserApiKey).where(
                    api_keys.user_id == user_id, api_keys.provider == provider
                )
            )
            if row is not None:
                await session.delete(row)
                await session.commit()

    async def _load(self, user_id: str, provider: str) -> UserApiKey | None:
        """按业务键读取密文行，未配置时返回 None。"""
        api_keys = metadata.tables["user_api_key"].c
        async with self.session_factory() as session:
            row = await session.scalar(
                select(UserApiKey).where(
                    api_keys.user_id == user_id, api_keys.provider == provider
                )
            )
        return row if isinstance(row, UserApiKey) else None
