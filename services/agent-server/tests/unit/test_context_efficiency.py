# 文件职责：验证工具结果压缩、技能正文折叠与 token 用量统计三项上下文优化。
# 定义 JSON 去重去空、shell 包装保留、技能折叠窗口与用量累计测试。
from __future__ import annotations

import json
from typing import Any

import pytest
from langchain.messages import AIMessage, ToolMessage

from travel_agent_agent.agents.common.context_efficiency import (
    SkillContentCollapseMiddleware,
    TokenUsageAccumulator,
    TokenUsageMiddleware,
    ToolResultCompressMiddleware,
    bind_token_usage,
    compress_tool_result,
    reset_token_usage,
)


def _payload() -> dict[str, Any]:
    """构造包含冗余 structuredContent、空值与黑名单字段的工具结果。"""
    return {
        "content": [{"type": "text", "text": '{"flights": [{"no": "MU5101"}]}'}],
        "structuredContent": {"flights": [{"no": "MU5101"}]},
        "airComImageUrl": ["http://cdn/x.png"],
        "queryId": "q-123",
        "emptyList": [],
        "emptyText": "   ",
        "zeroValue": 0,
        "falseValue": False,
        "flights": [{"no": "MU5101", "cabin": "", "seats": 3}],
    }


def test_compress_tool_result_removes_redundancy_and_empties() -> None:
    """压缩必须去重 structuredContent、删除空值与黑名单字段，并保留 0 / false。"""
    original = json.dumps(_payload(), ensure_ascii=False, indent=2)

    compressed = compress_tool_result(original)

    assert compressed is not None
    assert len(compressed) < len(original)
    restored = json.loads(compressed)
    assert "structuredContent" not in restored
    assert "airComImageUrl" not in restored
    assert "queryId" not in restored
    assert "emptyList" not in restored
    assert "emptyText" not in restored
    assert restored["zeroValue"] == 0
    assert restored["falseValue"] is False
    assert restored["flights"] == [{"no": "MU5101", "seats": 3}]


def test_compress_tool_result_keeps_non_json_and_small_payloads() -> None:
    """非 JSON 文本与无体积收益的内容必须原样保留。"""
    assert compress_tool_result("纯文本结果") is None
    assert compress_tool_result("") is None
    # 已是最紧凑形式时无收益，保持原文。
    assert compress_tool_result('{"a":1}') is None
    # 仅含空白的 JSON 会带来收益，返回紧凑形式。
    assert compress_tool_result('{"a": 1}') == '{"a":1}'


def test_compress_tool_result_caps_long_lists() -> None:
    """超长候选列表按条数截断并标记省略数量，避免工具消息超过网关长度上限。"""
    payload = {
        "data": [{"trainNum": f"G{index}", "price": {"wzPrice": "156.5"}} for index in range(30)]
    }
    original = json.dumps(payload, ensure_ascii=False, indent=2)

    compressed = compress_tool_result(original)

    assert compressed is not None
    restored = json.loads(compressed)
    assert len(restored["data"]) == 11
    assert restored["data"][-1] == {"_omitted": "另有 20 条已省略"}
    assert len(compressed) < 2000


def test_compress_tool_result_preserves_shell_wrapper() -> None:
    """execute_shell_command 的 returncode/stdout/stderr 包装必须保留，只压缩 stdout。"""
    wrapped = (
        "<returncode>0</returncode>"
        f"<stdout>{json.dumps(_payload(), ensure_ascii=False, indent=2)}</stdout>"
        "<stderr></stderr>"
    )

    compressed = compress_tool_result(wrapped)

    assert compressed is not None
    assert compressed.startswith("<returncode>0</returncode><stdout>{")
    assert compressed.endswith("</stderr>")
    assert "structuredContent" not in compressed


@pytest.mark.asyncio
async def test_compress_middleware_rewrites_tool_message() -> None:
    """中间件把工具消息内容替换为压缩后的 JSON，非 JSON 结果保持原样。"""
    middleware = ToolResultCompressMiddleware()
    original = json.dumps(_payload(), ensure_ascii=False, indent=2)

    async def handler(_: object) -> Any:
        """返回未压缩的工具消息。"""
        return ToolMessage(content=original, tool_call_id="c1")

    async def plain_handler(_: object) -> Any:
        """返回非 JSON 工具消息。"""
        return ToolMessage(content="普通结果", tool_call_id="c2")

    compressed = await middleware.awrap_tool_call(object(), handler)
    plain = await middleware.awrap_tool_call(object(), plain_handler)

    assert isinstance(compressed, ToolMessage)
    assert len(str(compressed.content)) < len(original)
    assert isinstance(plain, ToolMessage)
    assert plain.content == "普通结果"


def test_skill_collapse_replaces_only_old_skill_results() -> None:
    """只有超出保留窗口的技能正文被折叠，最近加载与普通工具结果保持完整。"""
    middleware = SkillContentCollapseMiddleware(keep_recent_messages=3)
    load_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "load_skill_through_path",
                "args": {"skill": "tuniu-cli", "path": "references/flight.md"},
                "id": "call_skill",
            }
        ],
    )
    skill_result = ToolMessage(
        content="整份技能正文" * 50, tool_call_id="call_skill", name="load_skill_through_path"
    )
    other_result = ToolMessage(content="普通工具结果", tool_call_id="call_other")
    request = _model_request(
        [
            load_call,
            skill_result,
            other_result,
            AIMessage(content="继续"),
            AIMessage(content="再继续"),
        ]
    )

    collapsed = middleware._collapse(request)

    contents = [getattr(message, "content", "") for message in collapsed.messages]
    assert "技能正文已折叠" in str(contents[1])
    assert "references/flight.md" in str(contents[1])
    assert contents[2] == "普通工具结果"
    # 未超出保留窗口时不折叠（阈值为消息数减去保留条数）。
    short_request = _model_request([load_call, skill_result, AIMessage(content="继续")])
    assert middleware._collapse(short_request) is short_request


@pytest.mark.asyncio
async def test_token_usage_middleware_accumulates_real_usage() -> None:
    """逐轮累计模型真实用量，并写入可发布的安全统计结构。"""
    token, accumulator = bind_token_usage()
    middleware = TokenUsageMiddleware()

    async def handler(_: object) -> Any:
        """返回带用量元数据的模型响应。"""
        return _model_response(
            [
                AIMessage(
                    content="ok",
                    usage_metadata={
                        "input_tokens": 120,
                        "output_tokens": 30,
                        "total_tokens": 150,
                    },
                )
            ]
        )

    try:
        await middleware.awrap_model_call(object(), handler)
        await middleware.awrap_model_call(object(), handler)
    finally:
        reset_token_usage(token)

    assert accumulator.model_calls == 2
    assert accumulator.input_tokens == 240
    assert accumulator.output_tokens == 60
    assert accumulator.total_tokens == 300
    assert accumulator.as_dict()["model_calls"] == 2


def test_token_usage_accumulator_ignores_missing_fields() -> None:
    """用量字段缺失时按 0 处理，不影响其它轮次累计。"""
    accumulator = TokenUsageAccumulator()

    accumulator.add({"input_tokens": None, "output_tokens": "12"})
    accumulator.add({})

    assert accumulator.input_tokens == 0
    assert accumulator.output_tokens == 12
    assert accumulator.model_calls == 2


def _model_request(messages: list[Any]) -> Any:
    """构造带 override 语义的最小模型请求替身。"""

    class _Request:
        """保存消息列表并支持 override。"""

        def __init__(self, items: list[Any]) -> None:
            """保存消息列表。"""
            self.messages = items

        def override(self, *, messages: list[Any]) -> _Request:
            """返回替换消息后的新请求。"""
            return _Request(messages)

    return _Request(messages)


def _model_response(messages: list[Any]) -> Any:
    """构造最小模型响应替身。"""
    return type("_Response", (), {"result": messages, "structured_response": None})()
