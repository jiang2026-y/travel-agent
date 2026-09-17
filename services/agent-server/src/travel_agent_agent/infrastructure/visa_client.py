# 文件职责：通过受限 Tool Gateway 调用 Orizn 签证 REST 能力。
# 定义 VisaClient 与 create_visa_client，负责动作映射、稳定错误归一与安全降级。
from __future__ import annotations

from typing import Any

from travel_agent_agent.core.settings import Settings
from travel_agent_agent.infrastructure.dashscope_client import (
    DashScopeGatewayError,
    ToolGatewayClient,
)

_FALLBACK_MESSAGE = "签证服务暂不可用，请稍后重试或查阅官方渠道。"


class VisaClient:
    """封装签证查询的只读网关调用，Agent 侧不接触任何外部密钥。"""

    def __init__(self, gateway: ToolGatewayClient) -> None:
        """保存共享受限网关客户端。"""
        self._gateway = gateway

    async def quick_check(self, passport: str, destination: str) -> dict[str, Any]:
        """快速判断护照与目的地是否需要签证。"""
        return await self._invoke(
            "quick_check", {"passport": passport, "destination": destination}
        )

    async def requirement(
        self, passport: str, destination: str, lang: str | None = None
    ) -> dict[str, Any]:
        """查询完整入境要求（材料、费用、时效等）。"""
        return await self._invoke(
            "requirement",
            {"passport": passport, "destination": destination, "lang": lang},
        )

    async def transit(
        self, passport: str, transit_country: str, lang: str | None = None
    ) -> dict[str, Any]:
        """查询经第三国中转时的过境签要求。"""
        return await self._invoke(
            "transit",
            {"passport": passport, "transit_country": transit_country, "lang": lang},
        )

    async def compare(
        self, passport: str, destinations: list[str], lang: str | None = None
    ) -> dict[str, Any]:
        """批量对比多个目的地的签证要求。"""
        return await self._invoke(
            "compare",
            {"passport": passport, "destinations": list(destinations), "lang": lang},
        )

    async def recent_changes(
        self,
        passport: str | None = None,
        destination: str | None = None,
        lang: str | None = None,
    ) -> dict[str, Any]:
        """查询近期签证政策变更。"""
        return await self._invoke(
            "changes",
            {"passport": passport, "destination": destination, "lang": lang},
        )

    async def coverage(self) -> dict[str, Any]:
        """查询签证数据覆盖范围，免 Key 即可调用。"""
        return await self._invoke("coverage", {})

    async def _invoke(self, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        """去掉空值后调用网关，并把网关稳定错误转换为可展示的降级结果。"""
        body = {key: value for key, value in payload.items() if value is not None}
        try:
            result = await self._gateway.post(f"/internal/v1/visa/{tool}", body)
        except DashScopeGatewayError as error:
            return {
                "available": False,
                "error_code": error.code,
                "retryable": error.retryable,
                "message": _FALLBACK_MESSAGE,
            }
        if not isinstance(result, dict):
            return {"available": False, "message": _FALLBACK_MESSAGE}
        return result


def create_visa_client(settings: Settings) -> VisaClient:
    """创建签证查询客户端，Orizn 密钥只保留在 Tool Gateway。"""
    settings.require_readonly_provider()
    return VisaClient(ToolGatewayClient(settings))
