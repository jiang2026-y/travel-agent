# 本文件验证受限 HTTP 客户端。
# 定义重定向拒绝和超大响应拒绝测试，确保远程 HTTP 工具不能绕过网关限制。

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from travel_agent_tool_gateway.http_client import RestrictedHttpClient, ToolResponseTooLarge
from travel_agent_tool_gateway.main import GatewaySettings, _dashscope_registry, create_app
from travel_agent_tool_gateway.policy import (
    OperationDefinition,
    ProviderDefinition,
    ProviderRegistry,
)


@pytest.mark.asyncio
async def test_redirect_response_is_not_followed() -> None:
    """远程服务返回重定向时，客户端必须拒绝而不是跟随新地址。"""
    registry = ProviderRegistry(
        [
            ProviderDefinition(
                provider_key="travel",
                base_url="https://readonly.example.test",
                allowed_hosts=("readonly.example.test",),
                operations=(
                    OperationDefinition(
                        operation_key="search",
                        method="GET",
                        path="/search",
                        read_only=True,
                    ),
                ),
            ),
        ],
    )
    client = RestrictedHttpClient(
        registry=registry,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                302, headers={"location": "https://untrusted.example.test"}
            ),
        ),
    )

    with pytest.raises(Exception, match="redirect_response_denied"):
        await client.execute("travel", "search")

    await client.aclose()


@pytest.mark.asyncio
async def test_response_larger_than_limit_is_rejected() -> None:
    """超过响应大小上限的远程结果必须拒绝，不得进入 Agent 上下文。"""
    registry = ProviderRegistry(
        [
            ProviderDefinition(
                provider_key="travel",
                base_url="https://readonly.example.test",
                allowed_hosts=("readonly.example.test",),
                operations=(
                    OperationDefinition(
                        operation_key="search",
                        method="GET",
                        path="/search",
                        read_only=True,
                    ),
                ),
            ),
        ],
    )
    client = RestrictedHttpClient(
        registry=registry,
        max_response_bytes=1,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"12")),
    )

    with pytest.raises(ToolResponseTooLarge):
        await client.execute("travel", "search")

    await client.aclose()


@pytest.mark.asyncio
async def test_network_timeout_is_not_retried_or_exposed_as_success() -> None:
    """网络超时必须直接失败，不能伪造工具成功结果。"""
    registry = ProviderRegistry(
        [
            ProviderDefinition(
                provider_key="travel",
                base_url="https://readonly.example.test",
                allowed_hosts=("readonly.example.test",),
                operations=(
                    OperationDefinition(
                        operation_key="search",
                        method="GET",
                        path="/search",
                        read_only=True,
                    ),
                ),
            ),
        ],
    )

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        """模拟网络超时。"""
        del request
        raise httpx.ConnectTimeout("network_timeout")

    client = RestrictedHttpClient(registry=registry, transport=httpx.MockTransport(timeout_handler))

    with pytest.raises(httpx.ConnectTimeout):
        await client.execute("travel", "search")

    await client.aclose()


def test_embedding_endpoint_requires_internal_token_and_uses_fixed_provider_path(tmp_path) -> None:
    """内部 embedding 路由必须验证独立 Token，且只能转发至固定百炼工作空间路径。"""
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
        """断言路由不能改写目标主机、模型、维度或认证头。"""
        assert request.url.host == "ws-afyh9lpghkjx1iz8.cn-beijing.maas.aliyuncs.com"
        assert request.url.path == "/compatible-mode/v1/embeddings"
        assert request.headers["authorization"] == f"Bearer {'k' * 32}"
        assert json.loads(request.content) == {
            "model": "text-embedding-v4", "input": ["查询航班"], "dimensions": 1024
        }
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * 1024}]})

    restricted_client = RestrictedHttpClient(registry, transport=httpx.MockTransport(handler))
    application = create_app(registry=registry, settings=settings, client=restricted_client)
    with TestClient(application) as client:
        denied = client.post("/internal/v1/dashscope/embeddings", json={"texts": ["查询航班"]})
        response = client.post(
            "/internal/v1/dashscope/embeddings",
            headers={"Authorization": f"Bearer {'g' * 32}"},
            json={"texts": ["查询航班"]},
        )

    assert denied.status_code == 401
    assert response.status_code == 200
    assert len(response.json()["data"][0]["embedding"]) == 1024
