# 文件职责：验证 Tool Gateway 按 Agent 与模型分组校验工具白名单。
# 定义工具白名单放行、越权拒绝、缺失 Agent 名拒绝与消息上限测试。
from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from travel_agent_tool_gateway.http_client import RestrictedHttpClient
from travel_agent_tool_gateway.main import (
    _AGENT_TOOL_NAMES,
    GatewaySettings,
    _dashscope_registry,
    create_app,
)


def _gateway(tmp_path) -> tuple[TestClient, str]:
    """构造带 MockTransport 的网关客户端与内部 Token。"""
    gateway_token_file = tmp_path / "agent_gateway_internal_token"
    api_key_file = tmp_path / "dashscope_api_key"
    gateway_token_file.write_text("g" * 32, encoding="utf-8")
    api_key_file.write_text("k" * 32, encoding="utf-8")
    settings = GatewaySettings(
        dashscope_api_key_file=str(api_key_file),
        agent_gateway_token_file=str(gateway_token_file),
    )
    registry = _dashscope_registry(settings)

    async def handler(request: httpx.Request) -> httpx.Response:
        """返回固定聊天响应，不回显请求正文。"""
        del request
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    restricted_client = RestrictedHttpClient(registry, transport=httpx.MockTransport(handler))
    application = create_app(registry=registry, settings=settings, client=restricted_client)
    return TestClient(application), "g" * 32


def _chat_body(model: str, tools: list[dict[str, object]] | None = None) -> dict[str, object]:
    """构造最小聊天请求体，可按需附带工具定义。"""
    body: dict[str, object] = {
        "model": model,
        "temperature": 0,
        "enable_thinking": False,
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "question"},
        ],
    }
    if tools is not None:
        body["tools"] = tools
    return body


def _tool(name: str) -> dict[str, object]:
    """构造符合网关校验的函数工具定义。"""
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def test_agent_tool_whitelist_allows_registered_tools(tmp_path) -> None:
    """主控、预订与信息 Agent 携带本组工具时应被放行。"""
    client, token = _gateway(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    cases = (
        ("masterAgent", "qwen3.7-plus", "ask_user"),
        ("masterAgent", "qwen3.7-plus", "itinerary_manage_agent"),
        ("bookingAgent", "qwen3.7-plus", "query_travel_order"),
        ("bookingAgent", "qwen3.7-plus", "execute_shell_command"),
        ("itineraryManageAgent", "qwen3.7-plus", "submit_travel_approval"),
        ("infoAgent", "glm-5.1", "retrieve_knowledge"),
        ("infoAgent", "glm-5.1", "query_travel_policy"),
    )
    for agent_name, model, tool_name in cases:
        response = client.post(
            "/internal/v1/dashscope/chat-completions",
            headers={**headers, "X-Agent-Name": agent_name},
            json=_chat_body(model, [_tool(tool_name)]),
        )
        assert response.status_code == 200, (agent_name, tool_name, response.text)


def test_tools_without_agent_name_are_rejected(tmp_path) -> None:
    """glm-5.1 携带工具但缺少 Agent 名时必须拒绝，避免意图识别路径被放行工具。"""
    client, token = _gateway(tmp_path)
    response = client.post(
        "/internal/v1/dashscope/chat-completions",
        headers={"Authorization": f"Bearer {token}"},
        json=_chat_body("glm-5.1", [_tool("retrieve_knowledge")]),
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "agent_name_required_for_tools"


def test_cross_agent_tool_and_model_mismatch_are_rejected(tmp_path) -> None:
    """跨 Agent 工具与模型错配都必须拒绝。"""
    client, token = _gateway(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    cross_agent = client.post(
        "/internal/v1/dashscope/chat-completions",
        headers={**headers, "X-Agent-Name": "infoAgent"},
        json=_chat_body("glm-5.1", [_tool("query_travel_order")]),
    )
    model_mismatch = client.post(
        "/internal/v1/dashscope/chat-completions",
        headers={**headers, "X-Agent-Name": "infoAgent"},
        json=_chat_body("qwen3.6-flash", [_tool("retrieve_knowledge")]),
    )
    assert cross_agent.status_code == 422
    assert cross_agent.json()["detail"] == "agent_tool_not_allowed"
    assert model_mismatch.status_code == 422
    assert model_mismatch.json()["detail"] == "agent_model_mismatch"


def test_thinking_mode_and_message_limit_are_enforced(tmp_path) -> None:
    """思考模式必须在 Agent 工具路径关闭，超过消息上限的请求被拒绝。"""
    client, token = _gateway(tmp_path)
    headers = {"Authorization": f"Bearer {token}", "X-Agent-Name": "masterAgent"}
    thinking = client.post(
        "/internal/v1/dashscope/chat-completions",
        headers=headers,
        json={**_chat_body("qwen3.7-plus", [_tool("ask_user")]), "enable_thinking": True},
    )
    too_many = client.post(
        "/internal/v1/dashscope/chat-completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "glm-5.1",
            "temperature": 0,
            "messages": [{"role": "user", "content": "x"}] * 129,
        },
    )
    assert thinking.status_code == 422
    assert thinking.json()["detail"] == "agent_thinking_must_be_disabled"
    assert too_many.status_code == 422
    assert json.dumps(too_many.json(), ensure_ascii=False)


def test_multi_turn_messages_and_langchain_name_field_are_accepted(tmp_path) -> None:
    """摘要与工具消息会带来 name 字段和更多历史消息，网关必须放行合法协议字段。"""
    client, token = _gateway(tmp_path)
    body = {
        "model": "qwen3.7-plus",
        "temperature": 0,
        "enable_thinking": False,
        "stream": False,
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "question", "name": "traveler"},
            {"role": "tool", "content": "result", "name": "ask_user", "tool_call_id": "c1"},
        ]
        + [{"role": "user", "content": "history"} for _ in range(120)],
    }
    response = client.post(
        "/internal/v1/dashscope/chat-completions",
        headers={"Authorization": f"Bearer {token}", "X-Agent-Name": "masterAgent"},
        json=body,
    )
    assert response.status_code == 200, response.text


def test_tool_count_limit_matches_largest_agent_toolset(tmp_path) -> None:
    """行程管理与预订工具集已超过 16 个，上限必须能容纳且仍有硬边界。"""
    client, token = _gateway(tmp_path)
    headers = {"Authorization": f"Bearer {token}", "X-Agent-Name": "bookingAgent"}
    allowed_names = sorted(_AGENT_TOOL_NAMES["bookingAgent"])
    accepted = client.post(
        "/internal/v1/dashscope/chat-completions",
        headers=headers,
        json=_chat_body("qwen3.7-plus", [_tool(name) for name in allowed_names]),
    )
    oversized = client.post(
        "/internal/v1/dashscope/chat-completions",
        headers=headers,
        json=_chat_body(
            "qwen3.7-plus", [_tool(f"unknown_{index}") for index in range(41)]
        ),
    )
    assert accepted.status_code == 200, accepted.text
    assert oversized.status_code == 422


def test_tool_message_content_allows_structured_results(tmp_path) -> None:
    """工具消息可携带结构化查询结果：20000 字符放行，超过 24000 字符仍拒绝。"""
    client, token = _gateway(tmp_path)
    headers = {"Authorization": f"Bearer {token}", "X-Agent-Name": "bookingAgent"}
    base = {
        "model": "qwen3.7-plus",
        "temperature": 0,
        "enable_thinking": False,
    }
    allowed = client.post(
        "/internal/v1/dashscope/chat-completions",
        headers=headers,
        json={
            **base,
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "question"},
                {"role": "tool", "content": "x" * 20000, "tool_call_id": "c1"},
            ],
        },
    )
    oversized = client.post(
        "/internal/v1/dashscope/chat-completions",
        headers=headers,
        json={
            **base,
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "question"},
                {"role": "tool", "content": "x" * 24001, "tool_call_id": "c1"},
            ],
        },
    )
    assert allowed.status_code == 200, allowed.text
    assert oversized.status_code == 422
