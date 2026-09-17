# 文件职责：验证工具熔断的状态机、中间件拦截与开关语义（对齐 Java ToolCircuitBreakerHook）。
# 定义默认配置解析、阈值开断、冷却期拦截、半开恢复、失败重开与关闭时不介入等用例。
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain.messages import ToolMessage

from travel_agent_agent.agents.common.circuit_breaker import (
    InMemoryCircuitBreakerState,
    ToolCircuitBreaker,
    ToolCircuitBreakerMiddleware,
    build_circuit_breaker_middleware,
    create_circuit_breaker,
    is_failure_result,
)
from travel_agent_agent.core.settings import Settings, ToolCircuitBreakerSettings


class _Clock:
    """可手动推进的测试时钟。"""

    def __init__(self, now: float = 1_000.0) -> None:
        """保存初始时间。"""
        self.now = now

    def __call__(self) -> float:
        """返回当前时间。"""
        return self.now


def _settings(**overrides: Any) -> ToolCircuitBreakerSettings:
    """构造启用状态的熔断配置，可按用例覆盖单个参数。"""
    values: dict[str, Any] = {
        "enabled": True,
        "monitored_tools": frozenset({"query_weather"}),
        "failure_threshold": 3,
        "initial_cooldown_seconds": 60,
        "backoff_multiplier": 2.0,
        "max_cooldown_seconds": 600,
        "redis_key_prefix": "tool:cb:",
        "state_ttl_seconds": 86_400,
    }
    values.update(overrides)
    return ToolCircuitBreakerSettings(**values)


def _breaker(clock: _Clock | None = None, **overrides: Any) -> ToolCircuitBreaker:
    """构造使用内存状态的熔断器。"""
    return ToolCircuitBreaker(
        _settings(**overrides), InMemoryCircuitBreakerState(), clock=clock
    )


def test_default_settings_disable_circuit_breaker() -> None:
    """默认关闭熔断，且默认只监控天气与资讯两个外部依赖工具。"""
    settings = Settings.from_environment(
        {
            "TOOL_GATEWAY_BASE_URL": "http://tool-gateway:8002",
            "API_SERVER_BASE_URL": "http://api-server:8000",
            "TRAVEL_AGENT_REDIS_URL": "redis://redis:6379/0",
        }
    )

    assert settings.tool_circuit_breaker.enabled is False
    assert settings.tool_circuit_breaker.monitored_tools == frozenset(
        {"query_weather", "query_destination_news"}
    )
    assert settings.tool_circuit_breaker.failure_threshold == 3
    assert settings.tool_circuit_breaker.initial_cooldown_seconds == 60
    assert settings.tool_circuit_breaker.max_cooldown_seconds == 600
    assert create_circuit_breaker(settings.tool_circuit_breaker) is None
    assert build_circuit_breaker_middleware(None) == []


def test_settings_parse_enabled_and_monitored_tools() -> None:
    """显式开启时读取白名单、阈值与退避参数。"""
    settings = Settings.from_environment(
        {
            "TOOL_GATEWAY_BASE_URL": "http://tool-gateway:8002",
            "API_SERVER_BASE_URL": "http://api-server:8000",
            "TRAVEL_AGENT_REDIS_URL": "redis://redis:6379/0",
            "TRAVEL_AGENT_TOOL_CIRCUIT_BREAKER_ENABLED": "true",
            "TRAVEL_AGENT_TOOL_CIRCUIT_MONITORED_TOOLS": "query_weather, check_visa_requirement",
            "TRAVEL_AGENT_TOOL_CIRCUIT_FAILURE_THRESHOLD": "2",
            "TRAVEL_AGENT_TOOL_CIRCUIT_BACKOFF_MULTIPLIER": "3",
        }
    )

    breaker_settings = settings.tool_circuit_breaker
    assert breaker_settings.enabled is True
    assert breaker_settings.monitored_tools == frozenset(
        {"query_weather", "check_visa_requirement"}
    )
    assert breaker_settings.failure_threshold == 2
    assert breaker_settings.cooldown_for_generation(1) == 60
    assert breaker_settings.cooldown_for_generation(2) == 180
    assert breaker_settings.cooldown_for_generation(9) == 600


@pytest.mark.asyncio
async def test_threshold_opens_and_blocks_until_cooldown_expires() -> None:
    """连续失败达到阈值后进入 OPEN 并在冷却期内拦截调用，冷却结束后放行探测。"""
    clock = _Clock()
    breaker = _breaker(clock)

    assert await breaker.blocked_reason("query_weather") is None
    assert await breaker.record_failure("query_weather") == 0
    assert await breaker.record_failure("query_weather") == 0
    assert await breaker.record_failure("query_weather") == 60
    assert await breaker.blocked_reason("query_weather") is not None

    clock.now += 61
    # 冷却结束进入半开期，允许一次探测调用。
    assert await breaker.blocked_reason("query_weather") is None


@pytest.mark.asyncio
async def test_half_open_failure_reopens_with_backoff_and_success_recovers() -> None:
    """半开期失败按指数退避重新熔断，半开期成功则完全恢复。"""
    clock = _Clock()
    breaker = _breaker(clock)
    for _ in range(3):
        await breaker.record_failure("query_weather")

    clock.now += 61
    assert await breaker.record_failure("query_weather") == 120
    assert await breaker.blocked_reason("query_weather") is not None

    clock.now += 121
    assert await breaker.blocked_reason("query_weather") is None
    await breaker.record_success("query_weather")

    assert await breaker.blocked_reason("query_weather") is None
    assert await breaker.state.is_open("query_weather") is False
    assert await breaker.state.failure_count("query_weather") == 0


@pytest.mark.asyncio
async def test_unmonitored_tools_are_never_counted() -> None:
    """不在白名单内的工具不写状态，也不受熔断影响。"""
    breaker = _breaker()

    for _ in range(5):
        assert await breaker.record_failure("query_travel_order") == 0

    assert await breaker.blocked_reason("query_travel_order") is None
    assert await breaker.state.failure_count("query_travel_order") == 0


@pytest.mark.asyncio
async def test_middleware_blocks_open_tool_without_calling_handler() -> None:
    """OPEN 期间中间件直接返回降级消息，且不触发真实工具调用。"""
    breaker = _breaker()
    for _ in range(3):
        await breaker.record_failure("query_weather")
    calls: list[str] = []

    async def handler(request: object) -> Any:
        """记录调用并返回正常结果。"""
        del request
        calls.append("called")
        return ToolMessage(content="ok", tool_call_id="c1")

    middleware = ToolCircuitBreakerMiddleware(breaker)
    result = await middleware.awrap_tool_call(
        _request("query_weather"), handler
    )

    assert calls == []
    assert isinstance(result, ToolMessage)
    assert "熔断" in str(result.content)


@pytest.mark.asyncio
async def test_middleware_counts_failures_and_resets_on_success() -> None:
    """中间件把失败结果计为失败、把成功结果清零，并让业务异常继续冒泡。"""
    breaker = _breaker()
    middleware = ToolCircuitBreakerMiddleware(breaker)

    async def failing(request: object) -> Any:
        """返回失败标记结果。"""
        del request
        return {"available": False, "error_code": "dashscope_gateway_unavailable"}

    async def healthy(request: object) -> Any:
        """返回正常结果。"""
        del request
        return {"available": True}

    async def raises(request: object) -> Any:
        """抛出业务异常。"""
        del request
        raise RuntimeError("provider_down")

    await middleware.awrap_tool_call(_request("query_weather"), failing)
    assert await breaker.state.failure_count("query_weather") == 1
    with pytest.raises(RuntimeError):
        await middleware.awrap_tool_call(_request("query_weather"), raises)
    assert await breaker.state.failure_count("query_weather") == 2
    await middleware.awrap_tool_call(_request("query_weather"), healthy)
    assert await breaker.state.failure_count("query_weather") == 0


def test_failure_result_detection() -> None:
    """失败识别只覆盖显式失败标记，正常结果与未知结构都视为成功。"""
    assert is_failure_result({"success": False, "error_code": "x"}) is True
    assert is_failure_result({"available": False}) is True
    assert is_failure_result({"status": "provider_error"}) is True
    assert is_failure_result(ToolMessage(content="boom", tool_call_id="c", status="error")) is True
    assert is_failure_result({"available": True, "current": {}}) is False
    assert is_failure_result(ToolMessage(content="ok", tool_call_id="c")) is False


def _request(tool_name: str) -> Any:
    """构造最小工具调用请求替身。"""
    return SimpleNamespace(tool_call={"name": tool_name, "id": "call_1", "args": {}})
