# 本文件定义受限远程 HTTP 客户端。
# 定义 RestrictedHttpClient、ToolRedirectDenied、ToolResponseTooLarge 与 RestrictedHttpResponse。
# 用于执行白名单内的只读请求并拒绝重定向和超大响应。

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit

import httpx

from travel_agent_tool_gateway.policy import (
    ProviderRegistry,
    ToolPolicyDenied,
    path_placeholders,
)


class ToolRedirectDenied(RuntimeError):
    """表示远程服务返回重定向，因安全原因被拒绝。"""


class ToolResponseTooLarge(RuntimeError):
    """表示远程响应超过允许的最大字节数。"""


class ToolPathParameterDenied(RuntimeError):
    """表示路径参数缺失或包含越界字符。"""


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

    async def execute_get(
        self,
        provider_key: str,
        operation_key: str,
        *,
        path_params: Mapping[str, str] | None = None,
        query_params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        read_timeout: float | None = None,
    ) -> RestrictedHttpResponse:
        """执行白名单内的只读 GET，路径占位、查询参数与请求头都受登记约束。"""
        return await self._execute_authorized(
            provider_key,
            operation_key,
            path_params=dict(path_params or {}),
            query_params=dict(query_params or {}),
            headers=dict(headers or {}),
            read_timeout=read_timeout,
        )

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

    async def execute_write_json(
        self,
        provider_key: str,
        operation_key: str,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
        read_timeout: float | None = None,
    ) -> RestrictedHttpResponse:
        """执行 Provider 显式声明的非只读 POST，仍受固定域名与路径约束。"""
        return await self._execute_authorized(
            provider_key,
            operation_key,
            json=dict(payload),
            headers=dict(headers),
            read_timeout=read_timeout,
            declared_write=True,
        )

    async def stream_json(
        self,
        provider_key: str,
        operation_key: str,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
        *,
        read_timeout: float | None = None,
    ) -> AsyncIterator[bytes]:
        """以流式方式转发已登记 Provider 的只读 POST，逐块产出上游原文。

        路径、域名、方法仍由注册表固定；不做重定向，不缓存整段响应，
        也不对内容做任何改写（调用方自行解析 SSE）。
        """
        authorized = self._registry.authorize(provider_key, operation_key)
        operation_path = _render_path(authorized.operation.path, {})
        request_url = urljoin(f"{authorized.provider.base_url}/", operation_path.lstrip("/"))
        if urlsplit(request_url).hostname not in authorized.provider.allowed_hosts:
            raise RuntimeError("request_host_not_allowlisted")
        timeout = httpx.Timeout(
            timeout=15.0,
            connect=3.0,
            read=read_timeout if read_timeout is not None else 60.0,
        )
        async with self._client.stream(
            authorized.operation.method,
            request_url,
            json=dict(payload),
            headers=dict(headers),
            timeout=timeout,
        ) as response:
            if 300 <= response.status_code < 400:
                raise ToolRedirectDenied("redirect_response_denied")
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                yield chunk

    async def _execute_authorized(
        self,
        provider_key: str,
        operation_key: str,
        json: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        read_timeout: float | None = None,
        path_params: dict[str, str] | None = None,
        query_params: dict[str, str] | None = None,
        declared_write: bool = False,
    ) -> RestrictedHttpResponse:
        """在已完成策略校验后构造一次固定目标请求，禁止调用方拼接 URL。"""
        authorized = (
            self._registry.authorize_write(provider_key, operation_key)
            if declared_write
            else self._registry.authorize(provider_key, operation_key)
        )
        operation_path = _render_path(authorized.operation.path, path_params or {})
        _require_query_params_allowed(authorized.operation.allowed_query_params, query_params or {})
        request_url = urljoin(
            f"{authorized.provider.base_url}/",
            operation_path.lstrip("/"),
        )
        request_host = urlsplit(request_url).hostname
        if request_host not in authorized.provider.allowed_hosts:
            raise RuntimeError("request_host_not_allowlisted")

        response = await self._client.request(
            authorized.operation.method,
            request_url,
            json=json,
            params=query_params or None,
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


def _render_path(template: str, path_params: Mapping[str, str]) -> str:
    """用经过校验的路径参数替换占位符，拒绝缺失、越界或含斜杠的取值。"""
    required = set(path_placeholders(template))
    provided = {key for key, value in path_params.items() if isinstance(value, str)}
    if required - provided:
        raise ToolPathParameterDenied("path_parameter_missing")
    if provided - required:
        raise ToolPathParameterDenied("path_parameter_not_registered")
    rendered = template
    for key in required:
        value = path_params[key].strip()
        if not value or "/" in value or ".." in value or "\\" in value:
            raise ToolPathParameterDenied("path_parameter_invalid")
        rendered = rendered.replace("{" + key + "}", quote(value, safe=""))
    return rendered


def _require_query_params_allowed(
    allowed: tuple[str, ...], query_params: Mapping[str, str]
) -> None:
    """拒绝未登记的查询参数，避免调用方扩展请求语义。"""
    if not query_params:
        return
    if not set(query_params) <= set(allowed):
        raise ToolPolicyDenied("query_parameter_not_registered")
