# 文件职责：通过受限 Tool Gateway 查询目的地天气与当地资讯。
# 定义 DestinationLiveClient，负责请求构造、稳定错误归一与未配置时的安全降级。
from __future__ import annotations

from typing import Any

from travel_agent_agent.core.settings import Settings
from travel_agent_agent.infrastructure.dashscope_client import (
    DashScopeGatewayError,
    ToolGatewayClient,
)


class DestinationLiveClient:
    """封装目的地实时查询的只读网关调用，Agent 侧不接触任何外部密钥。"""

    def __init__(self, gateway: ToolGatewayClient) -> None:
        """保存共享受限网关客户端。"""
        self._gateway = gateway

    async def query_weather(self, city: str, date: str | None = None) -> dict[str, Any]:
        """查询目的地天气，Provider 未配置或失败时返回可展示的降级结果。"""
        return await self._post(
            "/internal/v1/destination/weather",
            {"city": city.strip(), "date": (date or "").strip() or None},
            fallback_message="天气查询暂时不可用，请告知用户稍后重试。",
        )

    async def query_destination_news(
        self, city: str, topic: str | None = None
    ) -> dict[str, Any]:
        """查询目的地资讯，Provider 未配置或失败时返回可展示的降级结果。"""
        return await self._post(
            "/internal/v1/destination/news",
            {"city": city.strip(), "topic": (topic or "").strip() or None},
            fallback_message=(
                "目的地资讯查询未启用，请建议用户查阅当地官方渠道。"
            ),
        )

    async def _post(
        self, path: str, payload: dict[str, Any], *, fallback_message: str
    ) -> dict[str, Any]:
        """发送只读查询并对网关稳定错误做最小降级。"""
        try:
            body = await self._gateway.post(path, payload)
        except DashScopeGatewayError as error:
            return {
                "available": False,
                "error_code": error.code,
                "retryable": error.retryable,
                "message": fallback_message,
            }
        if not isinstance(body, dict):
            return {"available": False, "message": fallback_message}
        return body


def create_destination_live_client(settings: Settings) -> DestinationLiveClient:
    """创建目的地实时查询客户端，保持外部密钥不出 Tool Gateway。"""
    settings.require_readonly_provider()
    return DestinationLiveClient(ToolGatewayClient(settings))


__all__ = [
    "DestinationLiveClient",
    "create_destination_live_client",
]
