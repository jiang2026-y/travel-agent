# 文件职责：实现工具维度的熔断降级（对齐 Java ToolCircuitBreakerHook）。
# 定义 CircuitBreakerState 协议、InMemoryCircuitBreakerState、RedisCircuitBreakerState、
# ToolCircuitBreaker 与 build_circuit_breaker_middleware，负责连续失败计数、OPEN 冷却、
# 指数退避与半开恢复；未启用或未命中白名单时完全不介入工具调用。
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain.messages import ToolMessage
from redis.asyncio import Redis
from redis.exceptions import RedisError

from travel_agent_agent.core.settings import ToolCircuitBreakerSettings

_LOGGER = logging.getLogger("travel_agent_agent.tool_circuit_breaker")
_FIELD_GENERATION = "n"
_FIELD_OPENED_AT = "at"
_DEGRADED_TEMPLATE = (
    "工具 {tool} 已因连续失败被暂时熔断，本轮不再发起外部调用。"
    "请在回复中如实说明该信息暂时无法获取，并建议用户稍后重试或改用其它渠道。"
)
_FAILED_STATUSES = frozenset({"provider_error", "failed", "error", "unavailable"})

# 失败计数与 OPEN 状态使用与 Java 相同的两步脚本，保证并发安全与自动过期。
_INCR_FAILURE_SCRIPT = """
local value = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[1])
return value
"""
_OPEN_NEXT_GENERATION_SCRIPT = """
local generation = redis.call('HINCRBY', KEYS[1], ARGV[1], 1)
redis.call('HSET', KEYS[1], ARGV[2], ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[4])
return generation
"""


class CircuitBreakerState(Protocol):
    """定义熔断状态存储的最小异步契约，便于 Redis 与内存实现互换。"""

    async def failure_count(self, tool_name: str) -> int: ...

    async def increment_failure(self, tool_name: str) -> int: ...

    async def reset_failure(self, tool_name: str) -> None: ...

    async def is_open(self, tool_name: str) -> bool: ...

    async def generation(self, tool_name: str) -> int: ...

    async def opened_at(self, tool_name: str) -> float | None: ...

    async def open_next_generation(self, tool_name: str, now: float) -> int: ...

    async def clear_open(self, tool_name: str) -> None: ...


@dataclass(slots=True)
class _MemoryEntry:
    """保存内存实现中的单个工具状态。"""

    failures: int = 0
    generation: int = 0
    opened_at: float | None = None


class InMemoryCircuitBreakerState:
    """进程内熔断状态，用于 Redis 不可用时保持功能与单元测试。"""

    def __init__(self) -> None:
        """初始化空状态表。"""
        self._entries: dict[str, _MemoryEntry] = {}

    def _entry(self, tool_name: str) -> _MemoryEntry:
        """按工具名取状态，不存在时创建。"""
        return self._entries.setdefault(tool_name, _MemoryEntry())

    async def failure_count(self, tool_name: str) -> int:
        """返回当前连续失败次数。"""
        return self._entry(tool_name).failures

    async def increment_failure(self, tool_name: str) -> int:
        """自增连续失败次数并返回新值。"""
        entry = self._entry(tool_name)
        entry.failures += 1
        return entry.failures

    async def reset_failure(self, tool_name: str) -> None:
        """清零连续失败次数。"""
        self._entry(tool_name).failures = 0

    async def is_open(self, tool_name: str) -> bool:
        """判断工具是否处于 OPEN 状态。"""
        return self._entry(tool_name).opened_at is not None

    async def generation(self, tool_name: str) -> int:
        """返回当前降级代数。"""
        return self._entry(tool_name).generation

    async def opened_at(self, tool_name: str) -> float | None:
        """返回本次熔断的开启时间戳（秒）。"""
        return self._entry(tool_name).opened_at

    async def open_next_generation(self, tool_name: str, now: float) -> int:
        """进入 OPEN：代数加一并记录开启时间，返回新代数。"""
        entry = self._entry(tool_name)
        entry.generation += 1
        entry.opened_at = now
        return entry.generation

    async def clear_open(self, tool_name: str) -> None:
        """清除 OPEN 标记与代数，回到 CLOSED。"""
        entry = self._entry(tool_name)
        entry.opened_at = None
        entry.generation = 0


class RedisCircuitBreakerState:
    """基于 Redis 的跨节点熔断状态，键结构对齐 Java CircuitBreakStore。"""

    def __init__(self, client: Redis, prefix: str, ttl_seconds: int) -> None:
        """保存 Redis 客户端、键前缀与状态 TTL。"""
        self._client = client
        self._prefix = prefix
        self._ttl = ttl_seconds

    def _fail_key(self, tool_name: str) -> str:
        """返回连续失败计数的键。"""
        return f"{self._prefix}fail:{tool_name}"

    def _gen_key(self, tool_name: str) -> str:
        """返回降级代数与开启时间的键。"""
        return f"{self._prefix}gen:{tool_name}"

    async def failure_count(self, tool_name: str) -> int:
        """读取连续失败次数，缺失时视为 0。"""
        return _parse_int(await self._get(self._fail_key(tool_name)))

    async def increment_failure(self, tool_name: str) -> int:
        """原子自增失败次数并统一续期。"""
        return await self._eval(
            _INCR_FAILURE_SCRIPT, self._fail_key(tool_name), str(self._ttl)
        )

    async def reset_failure(self, tool_name: str) -> None:
        """删除失败计数键。"""
        await self._delete(self._fail_key(tool_name))

    async def is_open(self, tool_name: str) -> bool:
        """OPEN 状态以开启时间字段存在为准。"""
        return await self._hexists(self._gen_key(tool_name), _FIELD_OPENED_AT)

    async def generation(self, tool_name: str) -> int:
        """读取降级代数。"""
        return _parse_int(await self._hget(self._gen_key(tool_name), _FIELD_GENERATION))

    async def opened_at(self, tool_name: str) -> float | None:
        """读取熔断开启时间戳（毫秒转秒）。"""
        value = await self._hget(self._gen_key(tool_name), _FIELD_OPENED_AT)
        if value is None:
            return None
        parsed = _parse_int(value)
        return parsed / 1000 if parsed > 0 else None

    async def open_next_generation(self, tool_name: str, now: float) -> int:
        """原子递增代数、写入开启时间并续期，返回新代数。"""
        return await self._eval(
            _OPEN_NEXT_GENERATION_SCRIPT,
            self._gen_key(tool_name),
            _FIELD_GENERATION,
            _FIELD_OPENED_AT,
            str(int(now * 1000)),
            str(self._ttl),
        )

    async def clear_open(self, tool_name: str) -> None:
        """删除代数键，使工具回到 CLOSED。"""
        await self._delete(self._gen_key(tool_name))

    # 以下私有方法把 redis-py 存根的联合返回类型收口在一处，保持上层类型干净。
    async def _get(self, key: str) -> object:
        """读取字符串键。"""
        return await self._client.get(key)

    async def _hget(self, key: str, field: str) -> object:
        """读取哈希字段。"""
        return await self._client.hget(key, field)  # type: ignore[misc]

    async def _hexists(self, key: str, field: str) -> bool:
        """判断哈希字段是否存在。"""
        return bool(await self._client.hexists(key, field))  # type: ignore[misc]

    async def _eval(self, script: str, key: str, *args: str) -> int:
        """执行 Lua 脚本并返回整数结果。"""
        result = await self._client.eval(script, 1, key, *args)  # type: ignore[misc]
        return _parse_int(result)

    async def _delete(self, key: str) -> None:
        """删除键。"""
        await self._client.delete(key)


class ToolCircuitBreaker:
    """按工具名维护 CLOSED → OPEN → 半开 → CLOSED 的熔断状态机。"""

    def __init__(
        self,
        settings: ToolCircuitBreakerSettings,
        state: CircuitBreakerState,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """保存熔断配置、状态存储与可替换时钟。"""
        self.settings = settings
        self.state = state
        self._clock = clock or time.time

    async def blocked_reason(self, tool_name: str) -> str | None:
        """工具处于 OPEN 且冷却未结束时返回降级文案，否则返回 None 放行。"""
        if not self.settings.is_monitored(tool_name):
            return None
        try:
            if not await self.state.is_open(tool_name):
                return None
            if await self._cooldown_expired(tool_name):
                # 冷却结束进入半开期，放行一次探测调用。
                return None
        except (RedisError, OSError) as error:
            _LOGGER.warning(
                "tool_circuit_state_unavailable tool=%s error_type=%s",
                tool_name,
                type(error).__name__,
            )
            return None
        return _DEGRADED_TEMPLATE.format(tool=tool_name)

    async def record_success(self, tool_name: str) -> None:
        """成功调用后清零计数；半开期成功则完全恢复。"""
        if not self.settings.is_monitored(tool_name):
            return
        try:
            if await self.state.is_open(tool_name):
                await self.state.clear_open(tool_name)
                await self.state.reset_failure(tool_name)
                _LOGGER.info("tool_circuit_recovered tool=%s", tool_name)
                return
            if await self.state.failure_count(tool_name) > 0:
                await self.state.reset_failure(tool_name)
        except (RedisError, OSError) as error:
            _LOGGER.warning(
                "tool_circuit_state_unavailable tool=%s error_type=%s",
                tool_name,
                type(error).__name__,
            )

    async def record_failure(self, tool_name: str) -> int:
        """失败后累计计数，达到阈值或半开期再失败时进入 OPEN，返回当前冷却秒数。"""
        if not self.settings.is_monitored(tool_name):
            return 0
        try:
            half_open = await self.state.is_open(tool_name)
            if half_open:
                generation = await self.state.open_next_generation(tool_name, self._clock())
                cooldown = self.settings.cooldown_for_generation(generation)
                _LOGGER.warning(
                    "tool_circuit_reopened tool=%s cooldown_seconds=%s", tool_name, cooldown
                )
                return cooldown
            count = await self.state.increment_failure(tool_name)
            if count >= self.settings.failure_threshold:
                generation = await self.state.open_next_generation(tool_name, self._clock())
                cooldown = self.settings.cooldown_for_generation(generation)
                _LOGGER.warning(
                    "tool_circuit_opened tool=%s consecutive_failures=%s cooldown_seconds=%s",
                    tool_name,
                    count,
                    cooldown,
                )
                return cooldown
            return 0
        except (RedisError, OSError) as error:
            _LOGGER.warning(
                "tool_circuit_state_unavailable tool=%s error_type=%s",
                tool_name,
                type(error).__name__,
            )
            return 0

    async def _cooldown_expired(self, tool_name: str) -> bool:
        """判断当前 OPEN 周期是否已过冷却期。"""
        opened_at = await self.state.opened_at(tool_name)
        if opened_at is None:
            return True
        cooldown = self.settings.cooldown_for_generation(await self.state.generation(tool_name))
        if cooldown <= 0:
            return True
        return self._clock() >= opened_at + cooldown


def is_failure_result(result: object) -> bool:
    """识别工具返回中的失败标记；正常结果与中断信号都不会被判为失败。"""
    content = getattr(result, "content", result)
    status = getattr(result, "status", None)
    if status == "error":
        return True
    if isinstance(content, dict):
        if content.get("success") is False or content.get("available") is False:
            return True
        return str(content.get("status", "")).lower() in _FAILED_STATUSES
    return False


class ToolCircuitBreakerMiddleware(AgentMiddleware):
    """在工具调用前后接入熔断状态机；未配置熔断器时直接放行。"""

    def __init__(self, breaker: ToolCircuitBreaker) -> None:
        """保存熔断器实例。"""
        super().__init__()
        self.breaker = breaker

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        """OPEN 期间直接返回降级消息，其余情况执行工具并记录成功/失败。"""
        tool_name = _tool_name(request)
        blocked = await self.breaker.blocked_reason(tool_name)
        if blocked is not None:
            _LOGGER.info("tool_circuit_blocked tool=%s", tool_name)
            return ToolMessage(
                content=blocked, tool_call_id=_tool_call_id(request), status="success"
            )
        try:
            result = await handler(request)
        except BaseException as error:
            # 中断等控制流信号必须原样冒泡，只把真实异常计为失败。
            if _is_control_flow(error):
                raise
            await self.breaker.record_failure(tool_name)
            raise
        if is_failure_result(result):
            await self.breaker.record_failure(tool_name)
        else:
            await self.breaker.record_success(tool_name)
        return result


def build_circuit_breaker_middleware(
    breaker: ToolCircuitBreaker | None,
) -> list[AgentMiddleware]:
    """按需返回熔断中间件；未启用熔断时返回空列表以保持原有工具链。"""
    if breaker is None or not breaker.settings.enabled:
        return []
    return [ToolCircuitBreakerMiddleware(breaker)]


def create_circuit_breaker(
    settings: ToolCircuitBreakerSettings, redis: Redis | None = None
) -> ToolCircuitBreaker | None:
    """构造熔断器；未启用时返回 None，Redis 不可用时退回进程内状态。"""
    if not settings.enabled:
        return None
    state: CircuitBreakerState = (
        InMemoryCircuitBreakerState()
        if redis is None
        else RedisCircuitBreakerState(redis, settings.redis_key_prefix, settings.state_ttl_seconds)
    )
    return ToolCircuitBreaker(settings, state)


_configured_breaker: ToolCircuitBreaker | None = None


def configure_circuit_breaker(breaker: ToolCircuitBreaker | None) -> None:
    """在应用启动时登记全局熔断器，供各 Agent 装配共享同一份状态。"""
    global _configured_breaker
    _configured_breaker = breaker


def current_circuit_breaker() -> ToolCircuitBreaker | None:
    """返回当前登记的熔断器，未启用时为 None。"""
    return _configured_breaker


def configured_circuit_breaker_middleware() -> list[AgentMiddleware]:
    """返回全局熔断中间件；未启用时返回空列表，不改变既有工具链行为。"""
    return build_circuit_breaker_middleware(_configured_breaker)


def _tool_name(request: ToolCallRequest) -> str:
    """从工具调用请求中读取工具名。"""
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, dict):
        name = tool_call.get("name")
        if isinstance(name, str):
            return name
    tool = getattr(request, "tool", None)
    return str(getattr(tool, "name", "") or "")


def _tool_call_id(request: ToolCallRequest) -> str:
    """从工具调用请求中读取调用标识。"""
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, dict):
        identifier = tool_call.get("id")
        if isinstance(identifier, str):
            return identifier
    return ""


def _is_control_flow(error: BaseException) -> bool:
    """识别中断、取消等控制流信号，避免被误计为工具失败。"""
    from langgraph.errors import GraphBubbleUp

    return isinstance(error, (GraphBubbleUp, KeyboardInterrupt, SystemExit))


def _parse_int(value: object) -> int:
    """把 Redis 返回值解析为非负整数，异常值按 0 处理。"""
    if value is None:
        return 0
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0
