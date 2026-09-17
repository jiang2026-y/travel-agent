# 文件职责：实现上下文效率优化，对齐 Java 的 CliResultCompressHook、
# SkillContentCollapseHook 与 TokenUsageHook。
# 定义 compress_tool_result、ToolResultCompressMiddleware、SkillContentCollapseMiddleware、
# TokenUsageAccumulator 与 TokenUsageMiddleware：压缩工具结果 JSON、折叠过期技能正文、
# 逐轮累计真实 token 用量；只影响发送给模型的内容，不改写业务数据。
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain.messages import ToolMessage

_LOGGER = logging.getLogger("travel_agent_agent.context_efficiency")

# 对模型决策无用、只增大 token 的字段，取自 Java CliResultCompressHook 黑名单。
_DROP_KEYS = frozenset({"airComImageUrl", "queryId", "craftType", "planModel"})
_CONTENT_KEY = "content"
_STRUCTURED_CONTENT_KEY = "structuredContent"
_STDOUT_OPEN = "<stdout>"
_STDOUT_CLOSE = "</stdout>"
_KEEP_RECENT_MESSAGES = 10
# 查询类工具可能返回几十条候选，按条数截断以控制上下文体积（结果卡片另有 5 条上限）。
_MAX_LIST_ITEMS = 10
_LOAD_SKILL_TOOL = "load_skill_through_path"
_COLLAPSED_TEMPLATE = (
    "（技能正文已折叠以节省上下文。如需再次查看完整说明，"
    "请调用 load_skill_through_path，参数：{arguments}）"
)


def compress_tool_result(text: str) -> str | None:
    """压缩工具结果文本；无 JSON 或无体积收益时返回 None 表示保持原文。"""
    if not isinstance(text, str) or not text.strip():
        return None
    start = text.find(_STDOUT_OPEN)
    end = text.find(_STDOUT_CLOSE)
    if start >= 0 and end > start + len(_STDOUT_OPEN):
        inner = text[start + len(_STDOUT_OPEN) : end]
        compressed = _compress_json_text(inner)
        if compressed is None:
            return None
        rebuilt = f"{text[: start + len(_STDOUT_OPEN)]}{compressed}{text[end:]}"
        return rebuilt if len(rebuilt) < len(text) else None
    return _compress_json_text(text)


def _compress_json_text(text: str) -> str | None:
    """对 JSON 文本去重、去空、去冗余字段并紧凑序列化。"""
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    compact = json.dumps(_clean_node(payload), ensure_ascii=False, separators=(",", ":"))
    return compact if len(compact) < len(text) else None


def _clean_node(value: Any) -> Any:
    """递归清理节点：删除 structuredContent 冗余、空值与黑名单字段。"""
    if isinstance(value, dict):
        drop_structured = _has_content_array(value)
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key in _DROP_KEYS:
                continue
            if key == _STRUCTURED_CONTENT_KEY and drop_structured:
                continue
            node = _clean_node(item)
            if _is_empty_value(node):
                continue
            cleaned[key] = node
        return cleaned
    if isinstance(value, list):
        items: list[Any] = []
        for item in value:
            node = _clean_node(item)
            if _is_empty_value(node):
                continue
            items.append(node)
        if len(items) > _MAX_LIST_ITEMS:
            omitted = len(items) - _MAX_LIST_ITEMS
            items = items[:_MAX_LIST_ITEMS]
            items.append({"_omitted": f"另有 {omitted} 条已省略"})
        return items
    if isinstance(value, str):
        nested = _drill_down_nested_json(value)
        return nested if nested is not None else value
    return value


def _has_content_array(node: dict[str, Any]) -> bool:
    """判断节点是否同时携带非空 content 数组，可安全删除 structuredContent。"""
    content = node.get(_CONTENT_KEY)
    return isinstance(content, list) and bool(content)


def _drill_down_nested_json(value: str) -> str | None:
    """把内嵌 JSON 字符串压缩为紧凑形式；非 JSON 或体积不变时返回 None。"""
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    return _compress_json_text(stripped)


def _is_empty_value(value: Any) -> bool:
    """空值定义：None、空白字符串、空数组、空对象；数字 0 与 False 保留。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return not value
    return False


class ToolResultCompressMiddleware(AgentMiddleware):
    """在工具结果回填上下文前压缩 JSON 体积。"""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        """执行工具并对返回内容做无损压缩。"""
        result = await handler(request)
        return _compress_result(result)


def _compress_result(result: Any) -> Any:
    """压缩 ToolMessage 或字典结果中的 JSON 文本，无收益时原样返回。"""
    if isinstance(result, ToolMessage):
        content = result.content
        if isinstance(content, str):
            compressed = compress_tool_result(content)
            if compressed is not None:
                return result.model_copy(update={"content": compressed})
        return result
    if isinstance(result, dict):
        text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        compressed = compress_tool_result(text)
        if compressed is not None:
            try:
                return json.loads(compressed)
            except json.JSONDecodeError:
                return result
    return result


class SkillContentCollapseMiddleware(AgentMiddleware):
    """把已过期技能的正文折叠为可重载占位符，只影响本轮模型输入。"""

    def __init__(self, keep_recent_messages: int = _KEEP_RECENT_MESSAGES) -> None:
        """保存保留最近消息条数，保证刚加载的技能正文仍完整可见。"""
        super().__init__()
        self.keep_recent_messages = keep_recent_messages

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """折叠历史技能正文后调用模型。"""
        return await handler(self._collapse(request))

    def _collapse(self, request: ModelRequest) -> ModelRequest:
        """把超出保留窗口的技能加载结果替换为占位符。"""
        messages = list(request.messages)
        hints = _skill_reload_hints(messages)
        if not hints:
            return request
        threshold = len(messages) - self.keep_recent_messages
        changed = False
        collapsed: list[Any] = []
        for index, message in enumerate(messages):
            call_id = getattr(message, "tool_call_id", None)
            hint = hints.get(call_id) if isinstance(call_id, str) else None
            if hint is not None and isinstance(message, ToolMessage) and index < threshold:
                collapsed.append(
                    message.model_copy(
                        update={"content": _COLLAPSED_TEMPLATE.format(arguments=hint)}
                    )
                )
                changed = True
                continue
            collapsed.append(message)
        if not changed:
            return request
        return request.override(messages=collapsed)


def _skill_reload_hints(messages: list[Any]) -> dict[str, str]:
    """从历史消息中收集技能加载调用的重载参数，按 tool_call_id 索引。"""
    hints: dict[str, str] = {}
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            if not isinstance(call, dict) or call.get("name") != _LOAD_SKILL_TOOL:
                continue
            identifier = call.get("id")
            if not isinstance(identifier, str):
                continue
            hints[identifier] = _format_skill_arguments(call.get("args"))
    return hints


def _format_skill_arguments(args: Any) -> str:
    """把技能加载参数渲染为可直接复用的调用片段。"""
    if not isinstance(args, dict):
        return _LOAD_SKILL_TOOL
    parts = [f"{key}={value!r}" for key, value in args.items() if value is not None]
    return ", ".join(parts) if parts else _LOAD_SKILL_TOOL


class TokenUsageAccumulator:
    """累计单次 Run 内所有模型调用的真实 token 用量。"""

    def __init__(self) -> None:
        """初始化空计数。"""
        self.input_tokens = 0
        self.output_tokens = 0
        self.model_calls = 0

    def add(self, usage: dict[str, Any]) -> None:
        """累加一次模型调用用量，缺失字段按 0 处理。"""
        self.input_tokens += _as_int(usage.get("input_tokens"))
        self.output_tokens += _as_int(usage.get("output_tokens"))
        self.model_calls += 1

    @property
    def total_tokens(self) -> int:
        """返回输入与输出 token 之和。"""
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict[str, int]:
        """返回可写入诊断事件的安全统计结构。"""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "model_calls": self.model_calls,
        }


_token_usage: ContextVar[TokenUsageAccumulator | None] = ContextVar(
    "travel_agent_token_usage", default=None
)


def bind_token_usage() -> tuple[Token[TokenUsageAccumulator | None], TokenUsageAccumulator]:
    """为当前运行绑定新的用量累计器，返回上下文令牌与累计器。"""
    accumulator = TokenUsageAccumulator()
    return _token_usage.set(accumulator), accumulator


def reset_token_usage(token: Token[TokenUsageAccumulator | None]) -> None:
    """恢复上一次的用量累计上下文。"""
    _token_usage.reset(token)


class TokenUsageMiddleware(AgentMiddleware):
    """逐轮读取模型实际用量并累计到当前运行的累计器。"""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """调用模型并累计本次用量。"""
        response = await handler(request)
        accumulator = _token_usage.get()
        if accumulator is not None:
            for usage in _response_usages(response):
                accumulator.add(usage)
        return response


def _response_usages(response: ModelResponse) -> list[dict[str, Any]]:
    """提取响应中每条消息的 token 用量。"""
    usages: list[dict[str, Any]] = []
    for message in response.result or []:
        usage = getattr(message, "usage_metadata", None)
        if isinstance(usage, dict):
            usages.append(usage)
    return usages


def _as_int(value: Any) -> int:
    """把用量字段安全转换为非负整数。"""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def build_context_efficiency_middleware() -> list[AgentMiddleware]:
    """返回技能折叠、token 统计与工具结果压缩三个中间件。"""
    return [
        SkillContentCollapseMiddleware(),
        TokenUsageMiddleware(),
        ToolResultCompressMiddleware(),
    ]
