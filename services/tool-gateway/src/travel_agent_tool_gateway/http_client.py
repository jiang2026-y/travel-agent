# 本文件定义受限远程 HTTP 客户端。
# 定义 RestrictedHttpClient、ToolRedirectDenied、ToolResponseTooLarge 与 RestrictedHttpResponse。
# 用于执行白名单内的只读请求并拒绝重定向和超大响应。

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from travel_agent_tool_gateway.policy import ProviderRegistry


class ToolRedirectDenied(RuntimeError):
    """表示远程服务返回重定向，因安全原因被拒绝。"""


class ToolResponseTooLarge(RuntimeError):
    """表示远程响应超过允许的最大字节数。"""


@dataclass(frozen=True, slots=True)
class RestrictedHttpResponse:
    """表示经网关限制后可交给上层处理的远程响应。"""

    status_code: int
    content: bytes
    content_type: str | None


class RestrictedHttpClient:
    """执行已登记 Provider 的只读请求，并强制 HTTPS、无重定向和响应大小限制。"""

    def __init__(
        self,
        registry: ProviderRegistry,
        max_response_bytes: int = 1_048_576,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """创建固定超时、TLS 校验和禁止重定向的异步 HTTP 客户端。"""
        self._registry = registry
        self._max_response_bytes = max_response_bytes
        self._client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(timeout=15.0, connect=3.0, read=10.0),
            transport=transport,
        )

    async def execute(self, provider_key: str, operation_key: str) -> RestrictedHttpResponse:
        """执行一个已授权的只读操作，并在任何策略越界时失败。"""
        return await self._execute_authorized(provider_key, operation_key)

    async def execute_json(
        self,
        provider_key: str,
        operation_key: str,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
        read_timeout: float | None = None,
    ) -> RestrictedHttpResponse:
        """执行白名单内的只读推理 POST；请求正文和认证头只能由固定网关路由提供。"""
        return await self._execute_authorized(
            provider_key,
            operation_key,
            json=dict(payload),
            headers=dict(headers),
            read_timeout=read_timeout,
        )

    async def _execute_authorized(
        self,
        provider_key: str,
        operation_key: str,
        json: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        read_timeout: float | None = None,
    ) -> RestrictedHttpResponse:
        """在已完成策略校验后构造一次固定目标请求，禁止调用方拼接 URL。"""
        authorized = self._registry.authorize(provider_key, operation_key)
        request_url = urljoin(
            f"{authorized.provider.base_url}/",
            authorized.operation.path.lstrip("/"),
        )
        request_host = urlsplit(request_url).hostname
        if request_host not in authorized.provider.allowed_hosts:
            raise RuntimeError("request_host_not_allowlisted")

        response = await self._client.request(
            authorized.operation.method,
            request_url,
            json=json,
            headers=headers,
            timeout=(
                httpx.Timeout(timeout=15.0, connect=3.0, read=read_timeout)
                if read_timeout is not None
                else httpx.USE_CLIENT_DEFAULT
            ),
        )
        if 300 <= response.status_code < 400:
            raise ToolRedirectDenied("redirect_response_denied")
        response.raise_for_status()
        content = response.content
        if len(content) > self._max_response_bytes:
            raise ToolResponseTooLarge("response_too_large")
        return RestrictedHttpResponse(
            status_code=response.status_code,
            content=content,
            content_type=response.headers.get("content-type"),
        )

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端并释放连接资源。"""
        await self._client.aclose()
