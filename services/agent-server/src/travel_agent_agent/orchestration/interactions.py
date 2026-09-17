# 本文件实现 Redis 中的一次性 HITL 确认凭证与待交互状态存储。
# 定义 PendingInteraction、PendingInteractionStore、RedisPendingInteractionStore 与内存测试替身。
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from redis.asyncio import Redis
from redis.exceptions import WatchError

_INTERACTION_PREFIX = "travel-agent:hitl:v1:"
_MAX_CAS_ATTEMPTS = 3

InteractionKind = Literal["clarification", "approval"]
InteractionStatus = Literal["issued", "consumed", "rejected", "cancelled"]


class InteractionError(RuntimeError):
    """表示待交互不存在、身份不匹配、令牌失效或并发消费冲突。"""


@dataclass(frozen=True, slots=True)
class PendingInteraction:
    """保存恢复所需的安全交互元数据，不保存确认 Token 明文。"""

    interaction_id: str
    kind: InteractionKind
    status: InteractionStatus
    user_id: str
    conversation_id: str
    run_id: str
    thread_id: str
    graph_thread_id: str
    tool_name: str | None
    args_hash: str | None
    allowed_decisions: tuple[str, ...]
    summary: dict[str, Any]
    token_hash: str | None
    action_version: int
    created_at: str
    # 最近一次轮换前的令牌哈希：前端轮询会刷新令牌，宽限一版避免点击时令牌刚好过期。
    previous_token_hash: str | None = None

    def public_payload(self, confirmation_token: str | None = None) -> dict[str, Any]:
        """返回可安全交给 API 与前端的摘要，Token 仅在当前响应中附带。"""
        payload: dict[str, Any] = {
            "interaction_id": self.interaction_id,
            "kind": self.kind,
            "status": self.status,
            "tool_name": self.tool_name,
            "allowed_decisions": list(self.allowed_decisions),
            "summary": self.summary,
            "action_version": self.action_version,
        }
        if confirmation_token is not None:
            payload["confirmation_token"] = confirmation_token
        return payload

    def to_json(self) -> str:
        """以紧凑 JSON 保存至 Redis，排除任何明文 Token。"""
        return json.dumps(
            {
                "interaction_id": self.interaction_id,
                "kind": self.kind,
                "status": self.status,
                "user_id": self.user_id,
                "conversation_id": self.conversation_id,
                "run_id": self.run_id,
                "thread_id": self.thread_id,
                "graph_thread_id": self.graph_thread_id,
                "tool_name": self.tool_name,
                "args_hash": self.args_hash,
                "allowed_decisions": list(self.allowed_decisions),
                "summary": self.summary,
                "token_hash": self.token_hash,
                "previous_token_hash": self.previous_token_hash,
                "action_version": self.action_version,
                "created_at": self.created_at,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> PendingInteraction:
        """解析 Redis 内容并拒绝缺少身份或令牌边界字段的记录。"""
        try:
            payload = json.loads(raw)
            return cls(
                interaction_id=_required_string(payload, "interaction_id"),
                kind=cast(
                    InteractionKind,
                    _required_literal(payload, "kind", {"clarification", "approval"}),
                ),
                status=cast(
                    InteractionStatus,
                    _required_literal(
                        payload, "status", {"issued", "consumed", "rejected", "cancelled"}
                    ),
                ),
                user_id=_required_string(payload, "user_id"),
                conversation_id=_required_string(payload, "conversation_id"),
                run_id=_required_string(payload, "run_id"),
                thread_id=_required_string(payload, "thread_id"),
                graph_thread_id=_required_string(payload, "graph_thread_id"),
                tool_name=_optional_string(payload, "tool_name"),
                args_hash=_optional_string(payload, "args_hash"),
                allowed_decisions=tuple(_required_strings(payload, "allowed_decisions")),
                summary=_required_dict(payload, "summary"),
                token_hash=_optional_string(payload, "token_hash"),
                previous_token_hash=_optional_string(payload, "previous_token_hash"),
                action_version=_required_positive_int(payload, "action_version"),
                created_at=_required_string(payload, "created_at"),
            )
        except (TypeError, ValueError, KeyError) as error:
            raise InteractionError("interaction_payload_invalid") from error


class PendingInteractionStore(Protocol):
    """定义待确认交互的签发、查询、消费和失效边界。"""

    async def issue(self, interaction: PendingInteraction) -> tuple[PendingInteraction, str]: ...

    async def get_for_display(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> dict[str, Any] | None: ...

    async def consume(
        self,
        interaction_id: str,
        token: str,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
        tool_name: str,
        args_hash: str,
        decision: str,
    ) -> PendingInteraction: ...

    async def validate_decision(
        self,
        interaction_id: str,
        token: str | None,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
        decision: str,
    ) -> PendingInteraction: ...

    async def reject(
        self,
        interaction_id: str,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
    ) -> PendingInteraction: ...

    async def consume_answered(
        self,
        interaction_id: str,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
    ) -> PendingInteraction: ...

    async def hide_from_display(
        self,
        interaction_id: str,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
    ) -> None: ...

    async def cancel_run(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> None: ...


@dataclass(slots=True)
class RedisPendingInteractionStore:
    """使用 Redis Watch/Multi 原子签发、轮换和消费一次性确认 Token。"""

    client: Redis
    ttl_seconds: int = 15 * 60

    def __post_init__(self) -> None:
        """拒绝非正 TTL，防止确认凭证永久滞留。"""
        if self.ttl_seconds <= 0:
            raise ValueError("interaction_ttl_seconds_must_be_positive")

    async def issue(self, interaction: PendingInteraction) -> tuple[PendingInteraction, str]:
        """签发高熵 Token，并仅将其 SHA-256 哈希写入 Redis。"""
        token = _new_token() if interaction.kind == "approval" else ""
        stored = replace(
            interaction,
            status="issued",
            token_hash=_token_hash(token) if token else None,
            created_at=datetime.now(UTC).isoformat(),
        )
        key = _interaction_key(stored.run_id, stored.interaction_id)
        created = await self.client.set(key, stored.to_json(), ex=self.ttl_seconds, nx=True)
        if not created:
            raise InteractionError("interaction_already_exists")
        await self.client.set(
            _run_index_key(stored.run_id), stored.interaction_id, ex=self.ttl_seconds
        )
        return stored, token

    async def get_for_display(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> dict[str, Any] | None:
        """读取当前待交互；审批交互在查询时原子轮换 Token。"""
        interaction_id = await self.client.get(_run_index_key(run_id))
        if not interaction_id:
            return None
        key = _interaction_key(run_id, str(interaction_id))
        for _ in range(_MAX_CAS_ATTEMPTS):
            async with self.client.pipeline(transaction=True) as pipeline:
                try:
                    await pipeline.watch(key)
                    raw = await pipeline.get(key)
                    if not raw:
                        return None
                    interaction = PendingInteraction.from_json(str(raw))
                    _require_identity(interaction, user_id, conversation_id, run_id, thread_id)
                    if interaction.status != "issued":
                        return interaction.public_payload()
                    token = _new_token() if interaction.kind == "approval" else None
                    updated = replace(
                        interaction,
                        token_hash=_token_hash(token) if token else interaction.token_hash,
                        previous_token_hash=(
                            interaction.token_hash if token else interaction.previous_token_hash
                        ),
                        action_version=interaction.action_version + (1 if token else 0),
                    )
                    # redis-py 未给 Pipeline.multi 提供类型标注，这里仅为第三方存根缺口。
                    pipeline.multi()  # type: ignore[no-untyped-call]
                    pipeline.set(key, updated.to_json(), ex=self.ttl_seconds)
                    pipeline.set(
                        _run_index_key(run_id), interaction.interaction_id, ex=self.ttl_seconds
                    )
                    await pipeline.execute()
                    return updated.public_payload(token)
                except WatchError:
                    continue
        raise InteractionError("interaction_concurrent_update")

    async def consume(
        self,
        interaction_id: str,
        token: str,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
        tool_name: str,
        args_hash: str,
        decision: str,
    ) -> PendingInteraction:
        """原子消费匹配 Token，并校验其绑定的工具、参数与调用链。"""
        key = _interaction_key(run_id, interaction_id)
        for _ in range(_MAX_CAS_ATTEMPTS):
            async with self.client.pipeline(transaction=True) as pipeline:
                try:
                    await pipeline.watch(key)
                    raw = await pipeline.get(key)
                    if not raw:
                        raise InteractionError("interaction_not_found_or_expired")
                    interaction = PendingInteraction.from_json(str(raw))
                    _require_identity(interaction, user_id, conversation_id, run_id, thread_id)
                    if interaction.kind != "approval" or interaction.status != "issued":
                        raise InteractionError("interaction_not_consumable")
                    if decision not in interaction.allowed_decisions:
                        raise InteractionError("interaction_decision_not_allowed")
                    if (interaction.tool_name, interaction.args_hash) != (tool_name, args_hash):
                        raise InteractionError("interaction_action_mismatch")
                    if not token or not secrets.compare_digest(
                        interaction.token_hash or "", _token_hash(token)
                    ):
                        if not _token_matches(interaction, token):
                            raise InteractionError("confirmation_token_invalid")
                    updated = replace(
                        interaction,
                        status="consumed",
                        token_hash=None,
                        previous_token_hash=None,
                    )
                    pipeline.multi()  # type: ignore[no-untyped-call]
                    pipeline.set(key, updated.to_json(), ex=self.ttl_seconds)
                    pipeline.delete(_run_index_key(run_id))
                    await pipeline.execute()
                    return updated
                except WatchError:
                    continue
        raise InteractionError("interaction_concurrent_update")

    async def validate_decision(
        self,
        interaction_id: str,
        token: str | None,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
        decision: str,
    ) -> PendingInteraction:
        """在恢复图前校验交互归属和令牌，但不抢先消费写操作授权。"""
        raw = await self.client.get(_interaction_key(run_id, interaction_id))
        if not raw:
            raise InteractionError("interaction_not_found_or_expired")
        interaction = PendingInteraction.from_json(str(raw))
        _require_identity(interaction, user_id, conversation_id, run_id, thread_id)
        if interaction.status != "issued" or decision not in interaction.allowed_decisions:
            raise InteractionError("interaction_decision_not_allowed")
        if interaction.kind == "approval" and not _token_matches(interaction, token):
            raise InteractionError("confirmation_token_invalid")
        return interaction

    async def reject(
        self,
        interaction_id: str,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
    ) -> PendingInteraction:
        """标记澄清或审批交互已拒绝，并从 Run 当前索引移除。"""
        key = _interaction_key(run_id, interaction_id)
        raw = await self.client.get(key)
        if not raw:
            raise InteractionError("interaction_not_found_or_expired")
        interaction = PendingInteraction.from_json(str(raw))
        _require_identity(interaction, user_id, conversation_id, run_id, thread_id)
        updated = replace(interaction, status="rejected", token_hash=None, previous_token_hash=None)
        async with self.client.pipeline(transaction=True) as pipeline:
            pipeline.set(key, updated.to_json(), ex=self.ttl_seconds)
            pipeline.delete(_run_index_key(run_id))
            await pipeline.execute()
        return updated

    async def consume_answered(
        self,
        interaction_id: str,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
    ) -> PendingInteraction:
        """标记澄清交互已被用户回答，并从 Run 当前索引移除，避免卡片残留。"""
        key = _interaction_key(run_id, interaction_id)
        raw = await self.client.get(key)
        if not raw:
            raise InteractionError("interaction_not_found_or_expired")
        interaction = PendingInteraction.from_json(str(raw))
        _require_identity(interaction, user_id, conversation_id, run_id, thread_id)
        updated = replace(interaction, status="consumed", token_hash=None, previous_token_hash=None)
        async with self.client.pipeline(transaction=True) as pipeline:
            pipeline.set(key, updated.to_json(), ex=self.ttl_seconds)
            pipeline.delete(_run_index_key(run_id))
            await pipeline.execute()
        return updated

    async def hide_from_display(
        self,
        interaction_id: str,
        *,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
    ) -> None:
        """用户已提交决定后仅摘除 Run 当前索引，保留记录供写事务原子消费。

        审批决定提交后，图恢复执行仍需模型多轮推理才会真正调用写工具并消费
        Token；若期间仍向查询接口暴露同一交互，前端轮询会反复弹出同一张确认
        卡片，并与已经到达的助手回复冲突。这里只删除索引，交互记录与 Token
        哈希保持不变，因此后续 consume 仍能正常校验并消费。
        """
        key = _interaction_key(run_id, interaction_id)
        raw = await self.client.get(key)
        if not raw:
            return
        interaction = PendingInteraction.from_json(str(raw))
        _require_identity(interaction, user_id, conversation_id, run_id, thread_id)
        current = await self.client.get(_run_index_key(run_id))
        if current is not None and str(current) != interaction_id:
            # 已被更新的一轮交互占用索引时不越权摘除。
            return
        await self.client.delete(_run_index_key(run_id))

    async def cancel_run(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> None:
        """取消 Run 时删除当前待确认记录，使残留 Token 立即失效。"""
        interaction_id = await self.client.get(_run_index_key(run_id))
        if not interaction_id:
            return
        key = _interaction_key(run_id, str(interaction_id))
        raw = await self.client.get(key)
        if raw:
            interaction = PendingInteraction.from_json(str(raw))
            _require_identity(interaction, user_id, conversation_id, run_id, thread_id)
        await self.client.delete(key, _run_index_key(run_id))


@dataclass(slots=True)
class InMemoryPendingInteractionStore:
    """提供不访问 Redis 的单元测试替身，并保持 Token 消费语义一致。"""

    records: dict[str, PendingInteraction]
    # 已被用户提交决定的交互不再展示，但仍可被 consume 消费。
    hidden: set[str] = field(default_factory=set)

    async def issue(self, interaction: PendingInteraction) -> tuple[PendingInteraction, str]:
        """签发测试用 Token 并保存哈希化记录。"""
        if interaction.interaction_id in self.records:
            raise InteractionError("interaction_already_exists")
        token = _new_token() if interaction.kind == "approval" else ""
        stored = replace(
            interaction,
            status="issued",
            token_hash=_token_hash(token) if token else None,
            previous_token_hash=None,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.records[stored.interaction_id] = stored
        self.hidden.discard(stored.interaction_id)
        return stored, token

    async def get_for_display(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> dict[str, Any] | None:
        """读取 Run 的唯一待交互并在审批场景轮换 Token。"""
        interaction = next(
            (
                item
                for item in self.records.values()
                if item.run_id == run_id
                and item.status == "issued"
                and item.interaction_id not in self.hidden
            ),
            None,
        )
        if interaction is None:
            return None
        _require_identity(interaction, user_id, conversation_id, run_id, thread_id)
        token = _new_token() if interaction.kind == "approval" else None
        updated = replace(
            interaction,
            token_hash=_token_hash(token) if token else interaction.token_hash,
            previous_token_hash=(
                interaction.token_hash if token else interaction.previous_token_hash
            ),
            action_version=interaction.action_version + (1 if token else 0),
        )
        self.records[updated.interaction_id] = updated
        return updated.public_payload(token)

    async def consume(self, interaction_id: str, token: str, **context: str) -> PendingInteraction:
        """使用与 Redis 实现相同的身份、动作和哈希校验消费测试 Token。"""
        interaction = self.records.get(interaction_id)
        if interaction is None:
            raise InteractionError("interaction_not_found_or_expired")
        _require_identity(
            interaction,
            context["user_id"], context["conversation_id"], context["run_id"], context["thread_id"],
        )
        if interaction.kind != "approval" or interaction.status != "issued":
            raise InteractionError("interaction_not_consumable")
        if context["decision"] not in interaction.allowed_decisions:
            raise InteractionError("interaction_decision_not_allowed")
        if (interaction.tool_name, interaction.args_hash) != (
            context["tool_name"],
            context["args_hash"],
        ):
            raise InteractionError("interaction_action_mismatch")
        if not secrets.compare_digest(interaction.token_hash or "", _token_hash(token)):
            if not _token_matches(interaction, token):
                raise InteractionError("confirmation_token_invalid")
        updated = replace(interaction, status="consumed", token_hash=None, previous_token_hash=None)
        self.records[interaction_id] = updated
        return updated

    async def validate_decision(
        self, interaction_id: str, token: str | None, **context: str
    ) -> PendingInteraction:
        """在内存替身中校验恢复决定但不提前消费 Token。"""
        interaction = self.records.get(interaction_id)
        if interaction is None:
            raise InteractionError("interaction_not_found_or_expired")
        _require_identity(
            interaction,
            context["user_id"],
            context["conversation_id"],
            context["run_id"],
            context["thread_id"],
        )
        if (
            interaction.status != "issued"
            or context["decision"] not in interaction.allowed_decisions
        ):
            raise InteractionError("interaction_decision_not_allowed")
        if interaction.kind == "approval" and (
            not token
            or not secrets.compare_digest(interaction.token_hash or "", _token_hash(token))
        ):
            if not _token_matches(interaction, token):
                raise InteractionError("confirmation_token_invalid")
        return interaction

    async def reject(self, interaction_id: str, **context: str) -> PendingInteraction:
        """拒绝指定测试交互。"""
        interaction = self.records.get(interaction_id)
        if interaction is None:
            raise InteractionError("interaction_not_found_or_expired")
        _require_identity(
            interaction,
            context["user_id"], context["conversation_id"], context["run_id"], context["thread_id"],
        )
        updated = replace(interaction, status="rejected", token_hash=None, previous_token_hash=None)
        self.records[interaction_id] = updated
        return updated

    async def consume_answered(self, interaction_id: str, **context: str) -> PendingInteraction:
        """标记测试交互已被回答。"""
        interaction = self.records.get(interaction_id)
        if interaction is None:
            raise InteractionError("interaction_not_found_or_expired")
        _require_identity(
            interaction,
            context["user_id"],
            context["conversation_id"],
            context["run_id"],
            context["thread_id"],
        )
        updated = replace(interaction, status="consumed", token_hash=None, previous_token_hash=None)
        self.records[interaction_id] = updated
        self.hidden.discard(interaction_id)
        return updated

    async def hide_from_display(self, interaction_id: str, **context: str) -> None:
        """测试替身中把交互从展示集合摘除，但不影响后续 Token 消费。"""
        interaction = self.records.get(interaction_id)
        if interaction is None:
            return
        _require_identity(
            interaction,
            context["user_id"],
            context["conversation_id"],
            context["run_id"],
            context["thread_id"],
        )
        self.hidden.add(interaction_id)

    async def cancel_run(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> None:
        """删除指定 Run 的全部测试交互。"""
        for interaction_id, interaction in tuple(self.records.items()):
            if interaction.run_id != run_id:
                continue
            _require_identity(interaction, user_id, conversation_id, run_id, thread_id)
            self.hidden.discard(interaction_id)
            del self.records[interaction_id]


def canonical_args_hash(arguments: dict[str, Any]) -> str:
    """为工具参数生成稳定哈希，阻止确认后替换参数。"""
    canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _interaction_key(run_id: str, interaction_id: str) -> str:
    """返回按 Run 与交互隔离的 Redis 键。"""
    return f"{_INTERACTION_PREFIX}interaction:{run_id}:{interaction_id}"


def _run_index_key(run_id: str) -> str:
    """返回 Run 当前待交互索引 Redis 键。"""
    return f"{_INTERACTION_PREFIX}run:{run_id}:current"


def _new_token() -> str:
    """生成仅用于一次性确认的高熵不透明 Token。"""
    return secrets.token_urlsafe(32)


def _token_hash(token: str) -> str:
    """计算 Token 的不可逆 SHA-256 哈希以便 Redis 校验。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_matches(interaction: PendingInteraction, token: str | None) -> bool:
    """接受当前令牌或最近一次轮换前的令牌，避免前端轮询造成的点击竞态。"""
    if not token:
        return False
    digest = _token_hash(token)
    return secrets.compare_digest(
        interaction.token_hash or "", digest
    ) or secrets.compare_digest(interaction.previous_token_hash or "", digest)


def _require_identity(
    interaction: PendingInteraction, user_id: str, conversation_id: str, run_id: str, thread_id: str
) -> None:
    """拒绝跨用户、会话、Run 或线程的交互查询与消费。"""
    if (
        interaction.user_id,
        interaction.conversation_id,
        interaction.run_id,
        interaction.thread_id,
    ) != (
        user_id,
        conversation_id,
        run_id,
        thread_id,
    ):
        raise InteractionError("interaction_identity_mismatch")


def _required_string(payload: object, name: str) -> str:
    """读取非空字符串字段。"""
    value = payload.get(name) if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value:
        raise ValueError(name)
    return value


def _optional_string(payload: object, name: str) -> str | None:
    """读取可空字符串字段。"""
    value = payload.get(name) if isinstance(payload, dict) else None
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(name)
    return value


def _required_literal(payload: object, name: str, allowed: set[str]) -> str:
    """读取受限字符串字面量字段。"""
    value = _required_string(payload, name)
    if value not in allowed:
        raise ValueError(name)
    return value


def _required_strings(payload: object, name: str) -> list[str]:
    """读取非空字符串列表字段。"""
    value = payload.get(name) if isinstance(payload, dict) else None
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise ValueError(name)
    return value


def _required_dict(payload: object, name: str) -> dict[str, Any]:
    """读取对象字段。"""
    value = payload.get(name) if isinstance(payload, dict) else None
    if not isinstance(value, dict):
        raise ValueError(name)
    return value


def _required_positive_int(payload: object, name: str) -> int:
    """读取正整数版本字段。"""
    value = payload.get(name) if isinstance(payload, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(name)
    return value
