# 文件职责：验证目的地天气、目的地资讯与百炼长期记忆网关端点。
# 定义天气归一化、未配置降级与长期记忆未启用降级测试。
from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from travel_agent_tool_gateway.http_client import RestrictedHttpClient
from travel_agent_tool_gateway.main import GatewaySettings, _dashscope_registry, create_app

_WEATHER_PAYLOAD = {
    "current_condition": [
        {
            "temp_C": "21",
            "FeelsLikeC": "20",
            "humidity": "40",
            "windspeedKmph": "12",
            "weatherDesc": [{"value": "Sunny"}],
        }
    ],
    "weather": [
        {
            "date": "2026-09-16",
            "maxtempC": "25",
            "mintempC": "15",
            "hourly": [
                {
                    "time": "1200",
                    "tempC": "24",
                    "windspeedKmph": "10",
                    "chanceofrain": "10",
                    "weatherDesc": [{"value": "Sunny"}],
                }
            ],
        }
    ],
}


def _client(tmp_path, handler) -> tuple[TestClient, str]:
    """构造注入 MockTransport 的网关客户端与内部 Token。"""
    gateway_token_file = tmp_path / "agent_gateway_internal_token"
    api_key_file = tmp_path / "dashscope_api_key"
    gateway_token_file.write_text("g" * 32, encoding="utf-8")
    api_key_file.write_text("k" * 32, encoding="utf-8")
    settings = GatewaySettings(
        dashscope_api_key_file=str(api_key_file),
        agent_gateway_token_file=str(gateway_token_file),
    )
    registry = _dashscope_registry(settings)
    restricted_client = RestrictedHttpClient(registry, transport=httpx.MockTransport(handler))
    application = create_app(registry=registry, settings=settings, client=restricted_client)
    return TestClient(application), "g" * 32


def test_weather_is_normalized_and_only_calls_allowlisted_host(tmp_path) -> None:
    """天气查询只能访问 wttr.in 固定路径，并按 3 小时粒度归一化输出。"""
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        """记录目标主机与查询参数，并返回固定天气报文。"""
        seen["host"] = request.url.host
        seen["path"] = request.url.path
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, content=json.dumps(_WEATHER_PAYLOAD).encode())

    client, token = _client(tmp_path, handler)
    response = client.post(
        "/internal/v1/destination/weather",
        headers={"Authorization": f"Bearer {token}"},
        json={"city": "杭州", "date": None},
    )
    body = response.json()
    assert response.status_code == 200
    assert seen["host"] == "wttr.in"
    assert seen["path"].startswith("/")
    assert "format=j1" in seen["query"]
    assert body["source"] == "wttr.in"
    assert body["current"]["tempC"] == "21°C"
    assert body["forecast"][0]["hourly"][0]["time"] == "12:00"


def test_weather_beyond_free_range_degrades_without_upstream_call(tmp_path) -> None:
    """超出免费预报范围且未配置更长期 MCP 时，只返回 beyond_range 降级结果。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        """任何上游调用都视为失败。"""
        del request
        raise AssertionError("unexpected_upstream_call")

    client, token = _client(tmp_path, handler)
    response = client.post(
        "/internal/v1/destination/weather",
        headers={"Authorization": f"Bearer {token}"},
        json={"city": "杭州", "date": "2099-01-01"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["beyond_range"] is True
    assert body["available"] is False


def test_news_and_memory_degrade_when_secrets_are_absent(tmp_path) -> None:
    """未配置资讯 Key 与记忆库参数时，两个端点都必须安全降级。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        """未配置时不应发起任何上游请求。"""
        del request
        raise AssertionError("unexpected_upstream_call")

    client, token = _client(tmp_path, handler)
    headers = {"Authorization": f"Bearer {token}"}
    news = client.post(
        "/internal/v1/destination/news", headers=headers, json={"city": "杭州"}
    )
    record = client.post(
        "/internal/v1/bailian/memory/record",
        headers=headers,
        json={"user_id": "u_1", "content": "偏好靠窗座位"},
    )
    retrieve = client.post(
        "/internal/v1/bailian/memory/retrieve",
        headers=headers,
        json={"user_id": "u_1", "query": "座位偏好"},
    )
    assert news.json()["available"] is False
    assert record.json()["available"] is False
    assert retrieve.json()["available"] is False
    assert retrieve.json()["memories"] == ""


def test_memory_record_and_search_use_verified_endpoints(tmp_path) -> None:
    """记忆库读写必须命中百炼 v2 固定路径，并携带 Bearer 凭据与用户标识。"""
    gateway_token_file = tmp_path / "agent_gateway_internal_token"
    api_key_file = tmp_path / "dashscope_api_key"
    library_file = tmp_path / "bailian_memory_library_id"
    gateway_token_file.write_text("g" * 32, encoding="utf-8")
    api_key_file.write_text("k" * 32, encoding="utf-8")
    library_file.write_text("mem_library_0001", encoding="utf-8")
    settings = GatewaySettings(
        dashscope_api_key_file=str(api_key_file),
        agent_gateway_token_file=str(gateway_token_file),
        bailian_memory_library_id_file=str(library_file),
    )
    registry = _dashscope_registry(settings)
    seen: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        """记录目标路径、认证头与请求体，并返回记忆节点。"""
        content = request.content.decode() if request.content else "{}"
        seen.append(
            {
                "host": request.url.host,
                "path": request.url.path,
                "query": request.url.query.decode(),
                "authorization": request.headers.get("authorization"),
                "body": json.loads(content),
            }
        )
        if request.url.path.endswith("/add"):
            return httpx.Response(200, json={"memory_nodes": [{"content": "偏好靠窗座位"}]})
        return httpx.Response(
            200,
            json={
                "memory_nodes": [
                    {"memory_node_id": "n1", "content": "常飞国航", "status": "valid"},
                    {"memory_node_id": "n2", "content": "偏好靠窗座位", "status": "valid"},
                    {"memory_node_id": "n3", "content": "已失效偏好", "status": "invalid"},
                ]
            },
        )

    restricted_client = RestrictedHttpClient(registry, transport=httpx.MockTransport(handler))
    application = create_app(registry=registry, settings=settings, client=restricted_client)
    with TestClient(application) as client:
        headers = {"Authorization": f"Bearer {'g' * 32}"}
        record = client.post(
            "/internal/v1/bailian/memory/record",
            headers=headers,
            json={"user_id": "u_1", "content": "偏好靠窗座位"},
        )
        retrieve = client.post(
            "/internal/v1/bailian/memory/retrieve",
            headers=headers,
            json={"user_id": "u_1", "query": "座位偏好"},
        )

    assert record.status_code == 200 and record.json()["available"] is True
    assert retrieve.status_code == 200 and retrieve.json()["available"] is True
    assert retrieve.json()["memories"] == "常飞国航\n偏好靠窗座位"
    add_call, search_call = seen
    assert add_call["host"] == "dashscope.aliyuncs.com"
    assert add_call["path"] == "/api/v2/apps/memory/add"
    assert add_call["authorization"] == f"Bearer {'k' * 32}"
    assert add_call["body"]["memory_library_id"] == "mem_library_0001"
    assert add_call["body"]["messages"] == [{"role": "user", "content": "偏好靠窗座位"}]
    assert add_call["body"]["meta_data"] == {"source": "travel-agent"}
    assert search_call["path"] == "/api/v2/apps/memory/memory_nodes"
    assert search_call["query"] == "user_id=u_1&page_size=20&page_num=1"
    assert search_call["authorization"] == f"Bearer {'k' * 32}"


def test_memory_upstream_failure_degrades_without_leaking_body(tmp_path) -> None:
    """上游失败时必须降级为未可用，且不把上游正文透传给 Agent。"""
    gateway_token_file = tmp_path / "agent_gateway_internal_token"
    api_key_file = tmp_path / "dashscope_api_key"
    library_file = tmp_path / "bailian_memory_library_id"
    gateway_token_file.write_text("g" * 32, encoding="utf-8")
    api_key_file.write_text("k" * 32, encoding="utf-8")
    library_file.write_text("mem_library_0001", encoding="utf-8")
    settings = GatewaySettings(
        dashscope_api_key_file=str(api_key_file),
        agent_gateway_token_file=str(gateway_token_file),
        bailian_memory_library_id_file=str(library_file),
    )
    registry = _dashscope_registry(settings)

    async def handler(request: httpx.Request) -> httpx.Response:
        """模拟记忆库返回包含内部信息的未授权响应。"""
        del request
        return httpx.Response(401, json={"message": "Invalid api key sk-secret-value"})

    restricted_client = RestrictedHttpClient(registry, transport=httpx.MockTransport(handler))
    application = create_app(registry=registry, settings=settings, client=restricted_client)
    with TestClient(application) as client:
        response = client.post(
            "/internal/v1/bailian/memory/retrieve",
            headers={"Authorization": f"Bearer {'g' * 32}"},
            json={"user_id": "u_1", "query": "座位偏好"},
        )

    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["memories"] == ""
    assert "sk-secret-value" not in response.text
