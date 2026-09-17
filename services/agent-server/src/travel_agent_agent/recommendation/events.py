# 文件职责：保存和读取问题推荐的 Redis 短期事件流。
# 定义 RecommendationEventStore、RedisRecommendationEventStore 和内存测试替身。
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

_KEY_PREFIX = "travel-agent:recommendations:v1:"
_DEDUPE_PREFIX = "travel-agent:recommendations-dedupe:v1:"
_MAX_EVENTS = 100


class RecommendationEventError(RuntimeError):
    """表示推荐事件存储不可用。"""


class RecommendationEventStore(Protocol):
    """定义推荐事件幂等写入和游标读取边界。"""

    async def publish(
        self,
        run_id: str,
        trace_id: str,
        answer_version: str,
        items: list[str],
    ) -> dict[str, object] | None: ...

    async def list_events(
        self, run_id: str, last_event_id: str | None = None
    ) -> list[dict[str, object]]: ...

    async def publish_assistant_reply(
        self, run_id: str, trace_id: str, answer_version: str, content: str
    ) -> dict[str, object] | None: ...

    async def publish_diagnostic(
        self, run_id: str, trace_id: str, event_type: str, data: dict[str, object]
    ) -> dict[str, object] | None: ...

    async def publish_conversation_title(
        self, run_id: str, trace_id: str, title: str
    ) -> dict[str, object] | None: ...


@dataclass(slots=True)
class RedisRecommendationEventStore:
    """使用 Redis Stream 保存推荐事件并按 Run TTL 自动过期。"""

    client: Redis
    ttl_seconds: int

    async def publish(
        self, run_id: str, trace_id: str, answer_version: str, items: list[str]
    ) -> dict[str, object] | None:
        """以 Run 和答案版本幂等发布脱敏 recommendations 事件。"""
        dedupe_key = f"{_DEDUPE_PREFIX}{run_id}:{answer_version}"
        try:
            created = await self.client.set(dedupe_key, "1", ex=self.ttl_seconds, nx=True)
            if not created:
                return None
            event = _event_payload(run_id, trace_id, items)
            stream_id = await self.client.xadd(
                _stream_key(run_id),
                {"event": json.dumps(event, ensure_ascii=False)},
                maxlen=_MAX_EVENTS,
            )
            await self.client.expire(_stream_key(run_id), self.ttl_seconds)
            event["stream_id"] = stream_id
            return event
        except RedisError as error:
            raise RecommendationEventError("recommendation_event_store_unavailable") from error

    async def publish_assistant_reply(
        self, run_id: str, trace_id: str, answer_version: str, content: str
    ) -> dict[str, object] | None:
        """发布脱敏后的主回复事件，供 API 网关转发给浏览器。"""
        dedupe_key = f"{_DEDUPE_PREFIX}{run_id}:reply:{answer_version}"
        try:
            created = await self.client.set(dedupe_key, "1", ex=self.ttl_seconds, nx=True)
            if not created:
                return None
            event = _reply_event_payload(run_id, trace_id, content)
            stream_id = await self.client.xadd(
                _stream_key(run_id),
                {"event": json.dumps(event, ensure_ascii=False)},
                maxlen=_MAX_EVENTS,
            )
            await self.client.expire(_stream_key(run_id), self.ttl_seconds)
            event["stream_id"] = stream_id
            return event
        except RedisError as error:
            raise RecommendationEventError("recommendation_event_store_unavailable") from error

    async def publish_diagnostic(
        self, run_id: str, trace_id: str, event_type: str, data: dict[str, object]
    ) -> dict[str, object] | None:
        """鍙戝竷涓嶅惈鍑嵁鍜屾鏂囩殑鎵ц闃舵璇婃柇浜嬩欢銆?"""
        event = _diagnostic_event_payload(run_id, trace_id, event_type, data)
        try:
            stream_id = await self.client.xadd(
                _stream_key(run_id),
                {"event": json.dumps(event, ensure_ascii=False)},
                maxlen=_MAX_EVENTS,
            )
            await self.client.expire(_stream_key(run_id), self.ttl_seconds)
            event["stream_id"] = stream_id
            return event
        except RedisError as error:
            raise RecommendationEventError("recommendation_event_store_unavailable") from error

    async def list_events(
        self, run_id: str, last_event_id: str | None = None
    ) -> list[dict[str, object]]:
        """按 Redis Stream 游标读取当前 Run 的推荐事件。"""
        start = f"({last_event_id}" if last_event_id else "-"
        try:
            rows = await self.client.xrange(_stream_key(run_id), min=start, max="+")
        except RedisError as error:
            raise RecommendationEventError("recommendation_event_store_unavailable") from error
        result: list[dict[str, object]] = []
        for stream_id, values in rows:
            raw = values.get("event") if isinstance(values, dict) else None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            if not isinstance(raw, str):
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                event["stream_id"] = (
                    stream_id.decode() if isinstance(stream_id, bytes) else stream_id
                )
                result.append(event)
        return result

    async def publish_conversation_title(
        self, run_id: str, trace_id: str, title: str
    ) -> dict[str, object] | None:
        """发布会话标题事件，供 API Server 落库并通知浏览器刷新会话列表。"""
        dedupe_key = f"{_DEDUPE_PREFIX}{run_id}:title"
        try:
            created = await self.client.set(dedupe_key, "1", ex=self.ttl_seconds, nx=True)
            if not created:
                return None
            event = _conversation_title_event_payload(run_id, trace_id, title)
            stream_id = await self.client.xadd(
                _stream_key(run_id),
                {"event": json.dumps(event, ensure_ascii=False)},
                maxlen=_MAX_EVENTS,
            )
            await self.client.expire(_stream_key(run_id), self.ttl_seconds)
            event["stream_id"] = stream_id
            return event
        except RedisError as error:
            raise RecommendationEventError("recommendation_event_store_unavailable") from error


@dataclass(slots=True)
class InMemoryRecommendationEventStore:
    """供单元测试使用的推荐事件存储替身。"""

    events: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    dedupe: set[tuple[str, str]] = field(default_factory=set)

    async def publish(
        self, run_id: str, trace_id: str, answer_version: str, items: list[str]
    ) -> dict[str, object] | None:
        """在内存中执行与 Redis 实现一致的幂等发布。"""
        key = (run_id, answer_version)
        if key in self.dedupe:
            return None
        self.dedupe.add(key)
        event = _event_payload(run_id, trace_id, items)
        event["stream_id"] = f"{len(self.events.get(run_id, [])) + 1}-0"
        self.events.setdefault(run_id, []).append(event)
        return event

    async def publish_assistant_reply(
        self, run_id: str, trace_id: str, answer_version: str, content: str
    ) -> dict[str, object] | None:
        """在内存替身中幂等保存主回复事件。"""
        key = (run_id, f"reply:{answer_version}")
        if key in self.dedupe:
            return None
        self.dedupe.add(key)
        event = _reply_event_payload(run_id, trace_id, content)
        event["stream_id"] = f"{len(self.events.get(run_id, [])) + 1}-0"
        self.events.setdefault(run_id, []).append(event)
        return event

    async def publish_diagnostic(
        self, run_id: str, trace_id: str, event_type: str, data: dict[str, object]
    ) -> dict[str, object] | None:
        """鍦ㄥ唴瀛樻浛韬腑淇濆瓨璇婃柇浜嬩欢锛屾柟渚挎祴璇曚笌 SSE 琛ュ彂銆?"""
        event = _diagnostic_event_payload(run_id, trace_id, event_type, data)
        event["stream_id"] = f"{len(self.events.get(run_id, [])) + 1}-0"
        self.events.setdefault(run_id, []).append(event)
        return event

    async def list_events(
        self, run_id: str, last_event_id: str | None = None
    ) -> list[dict[str, object]]:
        """按简单事件 ID 游标读取内存推荐事件。"""
        rows = self.events.get(run_id, [])
        if not last_event_id:
            return list(rows)
        return [item for item in rows if str(item.get("stream_id", "")) > last_event_id]

    async def publish_conversation_title(
        self, run_id: str, trace_id: str, title: str
    ) -> dict[str, object] | None:
        """在内存替身中保存会话标题事件。"""
        key = (run_id, "title")
        if key in self.dedupe:
            return None
        self.dedupe.add(key)
        event = _conversation_title_event_payload(run_id, trace_id, title)
        event["stream_id"] = f"{len(self.events.get(run_id, [])) + 1}-0"
        self.events.setdefault(run_id, []).append(event)
        return event


def _stream_key(run_id: str) -> str:
    """构造按 Run 隔离的 Redis Stream Key。"""
    return f"{_KEY_PREFIX}{run_id}"


def _event_payload(run_id: str, trace_id: str, items: list[str]) -> dict[str, object]:
    """构造符合 SSE v1 的推荐事件，不包含对话正文。"""
    return {
        "event_id": f"rec_{uuid.uuid4().hex}",
        "run_id": run_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "type": "recommendations",
        "trace_id": trace_id,
        "data": {"items": items[:10]},
    }


def _reply_event_payload(run_id: str, trace_id: str, content: str) -> dict[str, object]:
    """构造不含内部凭据和思维链的主回复事件。"""
    return {
        "event_id": f"msg_{uuid.uuid4().hex}",
        "run_id": run_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "type": "assistant_message",
        "trace_id": trace_id,
        "data": {"content": content[:16000]},
    }


def _diagnostic_event_payload(
    run_id: str, trace_id: str, event_type: str, data: dict[str, object]
) -> dict[str, object]:
    """鏋勯€犳棤鍑嵁銆佹棤鎬濈淮閾剧殑瀹夊叏璇婃柇浜嬩欢銆?"""
    return {
        "event_id": f"diag_{uuid.uuid4().hex}",
        "run_id": run_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "type": event_type,
        "trace_id": trace_id,
        "data": data,
    }


def _conversation_title_event_payload(
    run_id: str, trace_id: str, title: str
) -> dict[str, object]:
    """构造只包含标题文本的会话标题事件，不携带用户问题原文。"""
    return {
        "event_id": f"title_{uuid.uuid4().hex}",
        "run_id": run_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "type": "conversation_title",
        "trace_id": trace_id,
        "data": {"title": title[:64]},
    }
