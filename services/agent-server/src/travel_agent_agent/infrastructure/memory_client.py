# 文件职责：通过受限 Tool Gateway 读写百炼长期记忆（差旅偏好）。
# 定义 BailianMemoryClient 与 create_bailian_memory_client，未配置时统一降级为空结果。
from __future__ import annotations

from typing import Any

from travel_agent_agent.core.settings import Settings
from travel_agent_agent.infrastructure.dashscope_client import (
    DashScopeGatewayError,
    ToolGatewayClient,
)


class BailianMemoryClient:
    """封装百炼长期记忆的只读召回与偏好写入，密钥只保留在 Tool Gateway。"""

    def __init__(self, gateway: ToolGatewayClient) -> None:
        """保存共享受限网关客户端。"""
        self._gateway = gateway

    async def record(self, user_id: str, content: str) -> dict[str, Any]:
        """写入一条差旅偏好记忆；未配置或失败时返回可降级的结果。"""
        return await self._post(
            "/internal/v1/bailian/memory/record",
            {"user_id": user_id, "content": content},
            fallback_message="长期记忆当前未启用，本次偏好未被保存。",
        )

    async def retrieve(self, user_id: str, query: str) -> dict[str, Any]:
        """召回与当前话题相关的差旅偏好；未配置或失败时返回空召回。"""
        return await self._post(
            "/internal/v1/bailian/memory/retrieve",
            {"user_id": user_id, "query": query},
            fallback_message="长期记忆当前未启用，未召回历史偏好。",
        )

    async def _post(
        self, path: str, payload: dict[str, Any], *, fallback_message: str
    ) -> dict[str, Any]:
        """发送只读或受限写入请求，并把网关稳定错误转换为降级结果。"""
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


def create_bailian_memory_client(settings: Settings) -> BailianMemoryClient:
    """创建长期记忆客户端，AccessKey 与记忆库参数均不出 Tool Gateway。"""
    settings.require_readonly_provider()
    return BailianMemoryClient(ToolGatewayClient(settings))
