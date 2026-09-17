# 文件职责：登记并中断当前节点上正在执行的 Run，支持跨节点 Redis 广播。
# 定义 RunExecution、RunExecutionRegistry、InterruptTransport、NullInterruptTransport、
# RedisInterruptTransport 与 RunInterruptCoordinator，负责时序校验、任务取消与幂等清理。
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

_LOGGER = logging.getLogger("travel_agent_agent.interrupt")
INTERRUPT_CHANNEL = "agent:interrupt"
_PAYLOAD_SEPARATOR = "|"
_SUBSCRIBE_RETRY_SECONDS = 2.0

CleanupCallback = Callable[[str, str, str, str], Awaitable[None]]
BroadcastHandler = Callable[[str, int, str], Awaitable[None]]


@dataclass(slots=True)
class RunExecution:
    """保存一次在途运行的任务句柄、注册时刻与身份标识。"""

    run_id: str
    conversation_id: str
    task: asyncio.Task[object]
    registered_at_ms: int
    user_id: str
    thread_id: str


class RunExecutionRegistry:
    """按会话登记在途运行，并支持带时序校验的本地取消。"""

    def __init__(self) -> None:
        """初始化空注册表，不在构造阶段触碰事件循环。"""
        self._entries: dict[str, dict[str, RunExecution]] = {}

    def register(
        self, conversation_id: str, run_id: str, *, user_id: str = "", thread_id: str = ""
    ) -> RunExecution:
        """登记当前正在执行的任务；同一 Run 重复登记时覆盖为最新任务。"""
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("run_execution_requires_task")
        execution = RunExecution(
            run_id=run_id,
            conversation_id=conversation_id,
            task=task,
            registered_at_ms=_now_ms(),
            user_id=user_id,
            thread_id=thread_id,
        )
        self._entries.setdefault(conversation_id, {})[run_id] = execution
        return execution

    def finish(self, conversation_id: str, run_id: str) -> None:
        """注销已结束的运行；会话下无剩余运行时移除会话条目。"""
        runs = self._entries.get(conversation_id)
        if runs is None:
            return
        runs.pop(run_id, None)
        if not runs:
            self._entries.pop(conversation_id, None)

    def running_run_ids(self, conversation_id: str) -> tuple[str, ...]:
        """返回指定会话当前在途的 Run 标识。"""
        return tuple(self._entries.get(conversation_id, {}))

    @property
    def active_count(self) -> int:
        """返回本节点在途运行总数，用于诊断与测试。"""
        return sum(len(runs) for runs in self._entries.values())

    def cancel_local(
        self,
        conversation_id: str,
        *,
        broadcast_time_ms: int | None = None,
        exclude_run_id: str | None = None,
    ) -> tuple[RunExecution, ...]:
        """取消本地匹配的在途任务；晚于广播时刻注册的新执行流会被跳过。"""
        runs = self._entries.get(conversation_id)
        if not runs:
            return ()
        cancelled: list[RunExecution] = []
        for run_id, execution in list(runs.items()):
            if exclude_run_id is not None and run_id == exclude_run_id:
                continue
            # 同一毫秒内注册的新执行流也必须被保护：仅取消严格早于广播时刻登记的任务。
            if broadcast_time_ms is not None and execution.registered_at_ms > broadcast_time_ms:
                _LOGGER.info(
                    "interrupt_skipped_new_execution conversation_id=%s run_id=%s",
                    conversation_id,
                    run_id,
                )
                continue
            execution.task.cancel()
            cancelled.append(execution)
        return tuple(cancelled)


class InterruptTransport(Protocol):
    """定义中断广播的启动、停止与发送边界。"""

    async def start(self, handler: BroadcastHandler) -> None: ...

    async def stop(self) -> None: ...

    async def broadcast(
        self, conversation_id: str, timestamp_ms: int, origin_id: str
    ) -> None: ...


class NullInterruptTransport:
    """单进程或测试环境使用的空广播通道，只保留本地中断语义。"""

    async def start(self, handler: BroadcastHandler) -> None:
        """无订阅行为，直接返回。"""
        del handler

    async def stop(self) -> None:
        """无连接需要关闭。"""

    async def broadcast(self, conversation_id: str, timestamp_ms: int, origin_id: str) -> None:
        """不做跨节点广播。"""
        del conversation_id, timestamp_ms, origin_id


class RedisInterruptTransport:
    """基于 Redis Pub/Sub 的中断广播，Redis 不可用时退避重试且不影响启动。"""

    def __init__(self, client: Redis) -> None:
        """保存订阅专用的 Redis 客户端，不在构造阶段建立连接。"""
        self._client = client
        self._handler: BroadcastHandler | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    @classmethod
    def from_url(cls, redis_url: str) -> RedisInterruptTransport:
        """按内部 Redis 地址创建独立订阅客户端。"""
        return cls(Redis.from_url(redis_url, decode_responses=True))

    async def start(self, handler: BroadcastHandler) -> None:
        """启动后台订阅任务；订阅失败只记录日志并退避重试。"""
        self._handler = handler
        self._stopping = False
        self._task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        """停止订阅任务并关闭客户端连接。"""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._client.aclose()

    async def broadcast(self, conversation_id: str, timestamp_ms: int, origin_id: str) -> None:
        """发布中断消息；发布失败只记录 warning，不影响本地取消结果。"""
        payload = _PAYLOAD_SEPARATOR.join(
            (conversation_id, str(timestamp_ms), origin_id)
        )
        try:
            await self._client.publish(INTERRUPT_CHANNEL, payload)
        except RedisError as error:
            _LOGGER.warning("interrupt_broadcast_failed error_type=%s", type(error).__name__)

    async def _consume(self) -> None:
        """持续订阅中断频道，异常时退避重连。"""
        while not self._stopping:
            try:
                pubsub = self._client.pubsub()
                await pubsub.subscribe(INTERRUPT_CHANNEL)
                async for message in pubsub.listen():
                    if self._stopping:
                        break
                    if message.get("type") != "message":
                        continue
                    parsed = _parse_broadcast(message.get("data"))
                    if parsed is None or self._handler is None:
                        continue
                    await self._handler(*parsed)
            except asyncio.CancelledError:
                raise
            except (RedisError, OSError, ValueError) as error:
                _LOGGER.warning(
                    "interrupt_subscribe_retry error_type=%s", type(error).__name__
                )
            finally:
                await _close_pubsub(locals().get("pubsub"))
            if not self._stopping:
                await asyncio.sleep(_SUBSCRIBE_RETRY_SECONDS)


class RunInterruptCoordinator:
    """组合注册表与广播通道，提供预中断、定向中断与广播处理。"""

    def __init__(
        self,
        registry: RunExecutionRegistry,
        transport: InterruptTransport | None = None,
        cleanup: CleanupCallback | None = None,
        origin_id: str | None = None,
    ) -> None:
        """保存注册表、广播通道、清理回调与本节点标识。"""
        self.registry = registry
        self.transport = transport or NullInterruptTransport()
        self.cleanup = cleanup
        self.origin_id = origin_id or uuid.uuid4().hex

    async def start(self) -> None:
        """启动广播订阅，交由注册表处理收到的中断消息。"""
        await self.transport.start(self.handle_broadcast)

    async def stop(self) -> None:
        """停止广播订阅并释放连接。"""
        await self.transport.stop()

    async def preempt(self, conversation_id: str) -> tuple[str, ...]:
        """新执行开始前打断同会话旧执行，并把中断广播到其它节点。"""
        timestamp_ms = _now_ms()
        cancelled = self.registry.cancel_local(
            conversation_id, broadcast_time_ms=timestamp_ms
        )
        await self.transport.broadcast(conversation_id, timestamp_ms, self.origin_id)
        await self._cleanup(cancelled)
        return tuple(execution.run_id for execution in cancelled)

    async def interrupt(self, conversation_id: str, run_id: str) -> tuple[str, ...]:
        """定向中断指定 Run 的本地执行并广播到其它节点。"""
        timestamp_ms = _now_ms()
        cancelled = tuple(
            execution
            for execution in self.registry.cancel_local(
                conversation_id, broadcast_time_ms=timestamp_ms
            )
            if execution.run_id == run_id
        )
        await self.transport.broadcast(conversation_id, timestamp_ms, self.origin_id)
        await self._cleanup(cancelled)
        return tuple(execution.run_id for execution in cancelled)

    async def handle_broadcast(
        self, conversation_id: str, timestamp_ms: int, origin_id: str = ""
    ) -> None:
        """处理来自其它节点的中断广播；本节点发出的消息已在本地处理过，直接跳过。"""
        if origin_id and origin_id == self.origin_id:
            return
        cancelled = self.registry.cancel_local(
            conversation_id, broadcast_time_ms=timestamp_ms
        )
        await self._cleanup(cancelled)

    async def _cleanup(self, executions: tuple[RunExecution, ...]) -> None:
        """清理被取消运行对应的待交互状态，失败只记录 warning。"""
        if self.cleanup is None:
            return
        for execution in executions:
            try:
                await self.cleanup(
                    execution.user_id,
                    execution.conversation_id,
                    execution.run_id,
                    execution.thread_id,
                )
            except Exception as error:  # noqa: BLE001 - 清理失败不影响中断结果
                _LOGGER.warning(
                    "interrupt_cleanup_failed run_id=%s error_type=%s",
                    execution.run_id,
                    type(error).__name__,
                )


def _now_ms() -> int:
    """返回当前毫秒时间戳，用于注册与广播的时序校验。"""
    return int(time.time() * 1000)


def _parse_broadcast(body: object) -> tuple[str, int, str] | None:
    """解析 ``conversation_id|timestamp|origin`` 广播体，兼容两段旧格式。"""
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="ignore")
    if not isinstance(body, str) or not body.strip():
        return None
    parts = [item.strip() for item in body.split(_PAYLOAD_SEPARATOR)]
    conversation_id = parts[0] if parts else ""
    if not conversation_id:
        return None
    timestamp_ms = _now_ms()
    if len(parts) >= 2:
        try:
            timestamp_ms = int(parts[1])
        except ValueError:
            timestamp_ms = _now_ms()
    origin_id = parts[2] if len(parts) >= 3 else ""
    return (conversation_id, timestamp_ms, origin_id)


async def _close_pubsub(pubsub: object | None) -> None:
    """尽力关闭订阅对象，失败不影响重连流程。"""
    if pubsub is None:
        return
    close = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
    if close is None:
        return
    try:
        await close()
    except Exception:  # noqa: BLE001 - 关闭失败已在重连路径兜底
        return
