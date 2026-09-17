# 文件职责：验证在途运行注册表、带时序校验的取消与中断广播协调。
# 定义注册与注销、取消在途任务、跳过新执行流、广播解析与清理回调测试。
from __future__ import annotations

import asyncio

import pytest

from travel_agent_agent.orchestration.execution import (
    RunExecution,
    RunExecutionRegistry,
    RunInterruptCoordinator,
    _parse_broadcast,
)


class _RecordingTransport:
    """记录广播调用并支持模拟订阅生命周期。"""

    def __init__(self) -> None:
        self.broadcasts: list[tuple[str, int]] = []
        self.origins: list[str] = []
        self.started = False
        self.stopped = False

    async def start(self, handler: object) -> None:
        """标记订阅已启动，不在测试中真正监听。"""
        del handler
        self.started = True

    async def stop(self) -> None:
        """标记订阅已停止。"""
        self.stopped = True

    async def broadcast(
        self, conversation_id: str, timestamp_ms: int, origin_id: str
    ) -> None:
        """记录带来源标识的广播内容。"""
        self.broadcasts.append((conversation_id, timestamp_ms))
        self.origins.append(origin_id)


async def _spawn_registered_worker(
    registry: RunExecutionRegistry,
    conversation_id: str,
    run_id: str,
    *,
    user_id: str = "u1",
    thread_id: str = "t1",
) -> tuple[asyncio.Task[None], RunExecution]:
    """启动一个像生产代码那样自行登记的在途任务，并返回句柄与登记信息。"""
    ready = asyncio.Event()
    holder: dict[str, RunExecution] = {}

    async def worker() -> None:
        holder["entry"] = registry.register(
            conversation_id, run_id, user_id=user_id, thread_id=thread_id
        )
        ready.set()
        try:
            await asyncio.sleep(30)
        finally:
            # 与生产代码一致：执行结束（含被取消）时注销登记。
            registry.finish(conversation_id, run_id)

    task = asyncio.create_task(worker())
    await ready.wait()
    return task, holder["entry"]


@pytest.mark.asyncio
async def test_register_finish_and_cancel_local_task() -> None:
    """登记后可被本地取消，注销后不再出现在在途列表。"""
    registry = RunExecutionRegistry()
    task, entry = await _spawn_registered_worker(registry, "conv-1", "run-1")
    assert entry.run_id == "run-1"
    assert registry.running_run_ids("conv-1") == ("run-1",)

    cancelled = registry.cancel_local("conv-1")
    assert tuple(item.run_id for item in cancelled) == ("run-1",)
    with pytest.raises(asyncio.CancelledError):
        await task

    registry.finish("conv-1", "run-1")
    assert registry.running_run_ids("conv-1") == ()
    assert registry.active_count == 0


@pytest.mark.asyncio
async def test_cancel_skips_execution_registered_after_broadcast() -> None:
    """早于广播时刻登记的运行被取消，晚于广播时刻的新执行流被跳过。"""
    registry = RunExecutionRegistry()
    old_task, old_entry = await _spawn_registered_worker(registry, "conv-1", "run-old")
    cancelled = registry.cancel_local(
        "conv-1", broadcast_time_ms=old_entry.registered_at_ms + 1
    )
    assert tuple(item.run_id for item in cancelled) == ("run-old",)
    with pytest.raises(asyncio.CancelledError):
        await old_task
    registry.finish("conv-1", "run-old")

    new_task, new_entry = await _spawn_registered_worker(registry, "conv-1", "run-new")
    skipped = registry.cancel_local(
        "conv-1", broadcast_time_ms=new_entry.registered_at_ms - 1
    )
    assert skipped == ()
    assert registry.running_run_ids("conv-1") == ("run-new",)
    new_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await new_task
    registry.finish("conv-1", "run-new")


@pytest.mark.asyncio
async def test_coordinator_preempts_and_broadcasts_with_cleanup() -> None:
    """预中断会取消同会话旧运行、广播消息并清理待交互状态。"""
    registry = RunExecutionRegistry()
    transport = _RecordingTransport()
    cleaned: list[tuple[str, str, str, str]] = []

    async def cleanup(user_id: str, conversation_id: str, run_id: str, thread_id: str) -> None:
        """记录清理调用。"""
        cleaned.append((user_id, conversation_id, run_id, thread_id))

    coordinator = RunInterruptCoordinator(registry, transport, cleanup)
    await coordinator.start()
    assert transport.started is True

    task, _ = await _spawn_registered_worker(registry, "conv-1", "run-old")
    preempted = await coordinator.preempt("conv-1")
    assert preempted == ("run-old",)
    assert cleaned == [("u1", "conv-1", "run-old", "t1")]
    assert transport.broadcasts and transport.broadcasts[0][0] == "conv-1"
    with pytest.raises(asyncio.CancelledError):
        await task
    registry.finish("conv-1", "run-old")

    await coordinator.stop()
    assert transport.stopped is True


@pytest.mark.asyncio
async def test_handle_broadcast_cancels_matching_local_run() -> None:
    """收到其它节点的广播后，只取消早于广播时刻登记的本地运行。"""
    registry = RunExecutionRegistry()
    coordinator = RunInterruptCoordinator(registry, _RecordingTransport())
    task, entry = await _spawn_registered_worker(registry, "conv-2", "run-2")
    await coordinator.handle_broadcast("conv-2", entry.registered_at_ms + 1)
    with pytest.raises(asyncio.CancelledError):
        await task
    registry.finish("conv-2", "run-2")
    assert registry.active_count == 0


@pytest.mark.asyncio
async def test_same_millisecond_registration_survives_broadcast() -> None:
    """本节点发出的广播不得回环取消同毫秒注册的新执行流。"""
    registry = RunExecutionRegistry()
    transport = _RecordingTransport()
    coordinator = RunInterruptCoordinator(registry, transport)
    task, entry = await _spawn_registered_worker(registry, "conv-3", "run-3")
    await coordinator.handle_broadcast(
        "conv-3", entry.registered_at_ms, coordinator.origin_id
    )
    assert registry.running_run_ids("conv-3") == ("run-3",)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    registry.finish("conv-3", "run-3")


@pytest.mark.asyncio
async def test_preempt_does_not_cancel_run_registered_after_broadcast() -> None:
    """预中断只取消更早的在途运行，本节点广播回环不得误杀新运行。"""
    registry = RunExecutionRegistry()
    transport = _RecordingTransport()
    coordinator = RunInterruptCoordinator(registry, transport)
    old_task, _ = await _spawn_registered_worker(registry, "conv-4", "run-old")
    preempted = await coordinator.preempt("conv-4")
    new_task, new_entry = await _spawn_registered_worker(registry, "conv-4", "run-new")
    _, broadcast_time = transport.broadcasts[0]
    await coordinator.handle_broadcast("conv-4", broadcast_time, coordinator.origin_id)

    assert preempted == ("run-old",)
    assert registry.running_run_ids("conv-4") == ("run-new",)
    assert transport.origins == [coordinator.origin_id]
    assert new_entry.registered_at_ms >= broadcast_time
    with pytest.raises(asyncio.CancelledError):
        await old_task
    new_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await new_task
    registry.finish("conv-4", "run-old")
    registry.finish("conv-4", "run-new")


def test_parse_broadcast_handles_legacy_and_invalid_payloads() -> None:
    """广播体支持三段新格式、两段旧格式，非法内容返回空。"""
    assert _parse_broadcast("conv-1|1700000000000|node-a") == (
        "conv-1",
        1700000000000,
        "node-a",
    )
    legacy_two_parts = _parse_broadcast("conv-1|1700000000000")
    assert legacy_two_parts == ("conv-1", 1700000000000, "")
    legacy = _parse_broadcast("conv-2")
    assert legacy is not None and legacy[0] == "conv-2" and legacy[2] == ""
    assert _parse_broadcast("") is None
    assert _parse_broadcast(None) is None
