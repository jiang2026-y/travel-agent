# 本文件实现可恢复 Run 的 Redis 检查点存储及内存测试替身。
# 定义 RunCheckpoint、RunCheckpointStore、RedisRunCheckpointStore 与 InMemoryRunCheckpointStore。
# 它们分别负责状态载荷、存储协议、生产读写和测试隔离。
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import WatchError

from travel_agent_agent.orchestration.routing import RunRoute
from travel_agent_agent.orchestration.state import RunState
from travel_agent_agent.orchestration.status import RunStatus

_CHECKPOINT_PREFIX = "travel-agent:run-checkpoint:v1:"
_MAX_CAS_ATTEMPTS = 3


class CheckpointError(RuntimeError):
    """表示检查点不存在、归属不匹配、并发冲突或基础设施不可用。"""


@dataclass(frozen=True, slots=True)
class RunCheckpoint:
    """保存恢复 Run 所需的最小身份、会话和状态信息，不保存任务正文。"""

    user_id: str
    conversation_id: str
    state: RunState
    route: RunRoute | None = None

    def to_json(self) -> str:
        """序列化为 Redis 字符串值，避免把 Pydantic 或 ORM 对象写入缓存。"""
        return json.dumps(
            {
                "user_id": self.user_id,
                "conversation_id": self.conversation_id,
                "run_id": self.state.run_id,
                "thread_id": self.state.thread_id,
                "status": self.state.status.value,
                "version": self.state.version,
                "route": self.route.to_dict() if self.route is not None else None,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> RunCheckpoint:
        """解析并验证 Redis 检查点，损坏内容一律按不可恢复处理。"""
        try:
            payload = json.loads(raw)
            return cls(
                user_id=_required_string(payload, "user_id"),
                conversation_id=_required_string(payload, "conversation_id"),
                state=RunState(
                    run_id=_required_string(payload, "run_id"),
                    thread_id=_required_string(payload, "thread_id"),
                    status=RunStatus(_required_string(payload, "status")),
                    version=_required_positive_int(payload, "version"),
                ),
                route=_optional_route(payload),
            )
        except (TypeError, ValueError, KeyError) as error:
            raise CheckpointError("checkpoint_payload_invalid") from error


class RunCheckpointStore(Protocol):
    """定义 Run 检查点的创建、读取、恢复和取消边界。"""

    async def start(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> RunCheckpoint: ...

    async def resume(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> RunCheckpoint: ...

    async def cancel(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> RunCheckpoint: ...

    async def apply_route(
        self,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
        route: RunRoute,
        target_status: RunStatus,
    ) -> RunCheckpoint: ...


@dataclass(slots=True)
class RedisRunCheckpointStore:
    """以 Redis Watch/Multi 保证单个 Run 状态变更的乐观并发安全。"""

    client: Redis
    ttl_seconds: int

    def __post_init__(self) -> None:
        """拒绝非正 TTL，避免检查点永久滞留或启动即过期。"""
        if self.ttl_seconds <= 0:
            raise ValueError("checkpoint_ttl_seconds_must_be_positive")

    async def start(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> RunCheckpoint:
        """幂等创建并进入 running；相同 Run 的重试不会覆盖既有状态。"""
        checkpoint = RunCheckpoint(
            user_id=user_id,
            conversation_id=conversation_id,
            state=RunState(run_id, thread_id, RunStatus.CREATED).transition(RunStatus.RUNNING),
        )
        key = _checkpoint_key(run_id)
        created = await self.client.set(key, checkpoint.to_json(), ex=self.ttl_seconds, nx=True)
        if created:
            return checkpoint
        existing = await self._load(key)
        _require_same_identity(existing, user_id, conversation_id, thread_id)
        return existing

    async def resume(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> RunCheckpoint:
        """恢复同一用户和线程的非终态 Run；running 重试保持幂等。"""
        return await self._transition(
            user_id, conversation_id, run_id, thread_id, RunStatus.RUNNING
        )

    async def cancel(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> RunCheckpoint:
        """取消同一用户和线程的 Run；终态重试只返回已保存状态。"""
        return await self._transition(
            user_id, conversation_id, run_id, thread_id, RunStatus.CANCELLED
        )

    async def apply_route(
        self,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
        route: RunRoute,
        target_status: RunStatus,
    ) -> RunCheckpoint:
        """原子保存 L0/L1 的主控路由摘要，并将 Run 转到运行或澄清状态。"""
        return await self._transition(
            user_id,
            conversation_id,
            run_id,
            thread_id,
            target_status,
            route,
        )

    async def close(self) -> None:
        """关闭 Redis 客户端连接池，供 FastAPI 生命周期结束时调用。"""
        await self.client.aclose()

    async def _transition(
        self,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
        target: RunStatus,
        route: RunRoute | None = None,
    ) -> RunCheckpoint:
        key = _checkpoint_key(run_id)
        for _ in range(_MAX_CAS_ATTEMPTS):
            async with self.client.pipeline(transaction=True) as pipeline:
                try:
                    await pipeline.watch(key)
                    raw = await pipeline.get(key)
                    checkpoint = _parse_checkpoint(raw)
                    _require_same_identity(checkpoint, user_id, conversation_id, thread_id)
                    if checkpoint.state.status in {
                        RunStatus.COMPLETED,
                        RunStatus.CANCELLED,
                        RunStatus.FAILED,
                    }:
                        return checkpoint
                    state = (
                        checkpoint.state
                        if checkpoint.state.status is target
                        else checkpoint.state.transition(target)
                    )
                    updated = replace(checkpoint, state=state, route=route or checkpoint.route)
                    if updated == checkpoint:
                        return checkpoint
                    pipeline.multi()  # type: ignore[no-untyped-call]
                    pipeline.set(key, updated.to_json(), ex=self.ttl_seconds)
                    await pipeline.execute()
                    return updated
                except WatchError:
                    continue
        raise CheckpointError("checkpoint_concurrent_update")

    async def _load(self, key: str) -> RunCheckpoint:
        """从 Redis 读取单个检查点并将缺失项统一转换为领域错误。"""
        return _parse_checkpoint(await self.client.get(key))


@dataclass(slots=True)
class InMemoryRunCheckpointStore:
    """供单元测试验证恢复语义的内存替身，不访问 Redis。"""

    checkpoints: dict[str, RunCheckpoint]

    async def start(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> RunCheckpoint:
        """创建或返回同身份的既有检查点。"""
        existing = self.checkpoints.get(run_id)
        if existing is not None:
            _require_same_identity(existing, user_id, conversation_id, thread_id)
            return existing
        checkpoint = RunCheckpoint(
            user_id=user_id,
            conversation_id=conversation_id,
            state=RunState(run_id, thread_id, RunStatus.CREATED).transition(RunStatus.RUNNING),
        )
        self.checkpoints[run_id] = checkpoint
        return checkpoint

    async def resume(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> RunCheckpoint:
        """恢复运行中状态或从可恢复澄清状态回到运行中。"""
        return self._transition(user_id, conversation_id, run_id, thread_id, RunStatus.RUNNING)

    async def cancel(
        self, user_id: str, conversation_id: str, run_id: str, thread_id: str
    ) -> RunCheckpoint:
        """将非终态检查点转为 cancelled。"""
        return self._transition(user_id, conversation_id, run_id, thread_id, RunStatus.CANCELLED)

    async def apply_route(
        self,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
        route: RunRoute,
        target_status: RunStatus,
    ) -> RunCheckpoint:
        """在内存替身中保存路由摘要，保持与 Redis 实现一致的恢复语义。"""
        return self._transition(
            user_id, conversation_id, run_id, thread_id, target_status, route
        )

    def _transition(
        self,
        user_id: str,
        conversation_id: str,
        run_id: str,
        thread_id: str,
        target: RunStatus,
        route: RunRoute | None = None,
    ) -> RunCheckpoint:
        checkpoint = self.checkpoints.get(run_id)
        if checkpoint is None:
            raise CheckpointError("checkpoint_not_found_or_expired")
        _require_same_identity(checkpoint, user_id, conversation_id, thread_id)
        if checkpoint.state.status in {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED}:
            return checkpoint
        state = (
            checkpoint.state
            if checkpoint.state.status is target
            else checkpoint.state.transition(target)
        )
        updated = replace(checkpoint, state=state, route=route or checkpoint.route)
        if updated == checkpoint:
            return checkpoint
        self.checkpoints[run_id] = updated
        return updated


def _checkpoint_key(run_id: str) -> str:
    """返回版本化、按 Run 隔离的 Redis 键。"""
    return f"{_CHECKPOINT_PREFIX}{run_id}"


def _parse_checkpoint(raw: str | None) -> RunCheckpoint:
    """解析 Redis 返回值，并把不存在的键规范为可识别的恢复失败原因。"""
    if not raw:
        raise CheckpointError("checkpoint_not_found_or_expired")
    return RunCheckpoint.from_json(raw)


def _optional_route(payload: object) -> RunRoute | None:
    """读取可选路由摘要，兼容早期未保存 route 字段的 Redis 检查点。"""
    if not isinstance(payload, dict) or payload.get("route") is None:
        return None
    return RunRoute.from_dict(payload["route"])


def _require_same_identity(
    checkpoint: RunCheckpoint, user_id: str, conversation_id: str, thread_id: str
) -> None:
    """拒绝跨用户、跨会话或跨线程复用相同 Run 标识。"""
    if (checkpoint.user_id, checkpoint.conversation_id, checkpoint.state.thread_id) != (
        user_id,
        conversation_id,
        thread_id,
    ):
        raise CheckpointError("checkpoint_identity_mismatch")


def _required_string(payload: object, name: str) -> str:
    """读取检查点中的非空字符串字段。"""
    value = payload.get(name) if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value:
        raise ValueError(name)
    return value


def _required_positive_int(payload: object, name: str) -> int:
    """读取检查点中的正整型版本字段。"""
    value = payload.get(name) if isinstance(payload, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(name)
    return value
