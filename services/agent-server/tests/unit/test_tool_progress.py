# 文件职责：验证工具级进度中间件的事件字段、排除清单与降级行为。
# 定义进度事件内容、调度工具排除、失败标记与无回调不报错测试。
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain.messages import ToolMessage

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.agents.common.tool_progress import (
    ToolProgressMiddleware,
    build_tool_progress_middleware,
    tool_result_status,
)


class _Recorder:
    """收集诊断回调发布的事件。"""

    def __init__(self) -> None:
        """初始化空事件列表。"""
        self.events: list[tuple[str, dict[str, object]]] = []

    async def publish(self, event_type: str, data: dict[str, object]) -> None:
        """保存事件类型与数据。"""
        self.events.append((event_type, data))


def _context(recorder: _Recorder | None) -> AgentContext:
    """构造带可选诊断回调的请求上下文。"""
    return AgentContext(
        trace_id="trace",
        conversation_id="conv",
        diagnostic_callback=recorder.publish if recorder else None,
    )


def _request(tool_name: str) -> Any:
    """构造最小工具调用请求替身。"""
    return SimpleNamespace(tool_call={"name": tool_name, "id": "call_1", "args": {}})


@pytest.mark.asyncio
async def test_progress_events_carry_agent_tool_and_duration() -> None:
    """正常调用应发布开始与完成事件，并带上工具名、Agent 名与耗时。"""
    recorder = _Recorder()
    middleware = ToolProgressMiddleware("infoAgent", lambda: _context(recorder))

    async def handler(_: Any) -> Any:
        """返回正常工具结果。"""
        return {"available": True}

    result = await middleware.awrap_tool_call(_request("query_weather"), handler)

    assert result == {"available": True}
    assert [event_type for event_type, _ in recorder.events] == [
        "tool_started",
        "tool_completed",
    ]
    started = recorder.events[0][1]
    completed = recorder.events[1][1]
    assert started == {"agent": "infoAgent", "tool": "query_weather"}
    assert completed["agent"] == "infoAgent"
    assert completed["tool"] == "query_weather"
    assert completed["status"] == "completed"
    assert isinstance(completed["duration_ms"], int)
    # 事件里不允许出现工具参数或结果正文。
    assert "args" not in completed and "content" not in completed


@pytest.mark.asyncio
async def test_failed_tool_marks_status_failed_and_reraises() -> None:
    """工具抛错时标记 failed 并保持异常原样冒泡（中断信号同样不被吞）。"""
    recorder = _Recorder()
    middleware = ToolProgressMiddleware("bookingAgent", lambda: _context(recorder))

    async def handler(_: Any) -> Any:
        """抛出业务异常。"""
        raise RuntimeError("provider_down")

    with pytest.raises(RuntimeError):
        await middleware.awrap_tool_call(_request("search_tuniu_flight"), handler)

    assert recorder.events[-1][1]["status"] == "failed"


@pytest.mark.asyncio
async def test_dispatch_tools_are_excluded_from_progress() -> None:
    """子智能体调度与用户澄清属于流程节点，不发布工具进度事件。"""
    recorder = _Recorder()
    middleware = ToolProgressMiddleware("masterAgent", lambda: _context(recorder))

    async def handler(_: Any) -> Any:
        """返回子智能体结果。"""
        return ToolMessage(content="子智能体回复", tool_call_id="call_1")

    await middleware.awrap_tool_call(_request("info_agent"), handler)
    await middleware.awrap_tool_call(_request("ask_user"), handler)

    assert recorder.events == []


@pytest.mark.asyncio
async def test_missing_callback_is_silent() -> None:
    """没有诊断回调时不发布事件，也不影响工具执行。"""
    middleware = ToolProgressMiddleware("infoAgent", lambda: None)

    async def handler(_: Any) -> Any:
        """返回失败标记结果。"""
        return {"available": False}

    result = await middleware.awrap_tool_call(_request("query_destination_news"), handler)

    assert result == {"available": False}
    assert build_tool_progress_middleware("infoAgent", lambda: None)


def test_tool_result_status_detection() -> None:
    """失败标记归一为 failed，其余结果视为 completed。"""
    assert tool_result_status({"available": False}) == "failed"
    assert tool_result_status({"success": False}) == "failed"
    assert tool_result_status({"status": "provider_error"}) == "failed"
    errored = ToolMessage(content="x", tool_call_id="c", status="error")
    assert tool_result_status(errored) == "failed"
    assert tool_result_status({"available": True}) == "completed"
    assert tool_result_status("普通文本") == "completed"
