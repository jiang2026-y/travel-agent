# 文件职责：封装 ItineraryManageAgent 到 API Server 内部差旅接口的安全调用。
# 定义 TravelManageApiClient，并统一透传用户身份、确认凭证和四类关联标识。
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from travel_agent_agent.agents.base import AgentContext


class TravelManageApiError(RuntimeError):
    """表示内部差旅 API 不可用或拒绝了请求。"""


class TravelManageApiClient:
    """通过 Docker 内部网络调用 API Server，不直接连接 PostgreSQL。"""

    def __init__(self, base_url: str, token_file: str, context: AgentContext) -> None:
        """保存内部地址、Secret 路径和本次 Agent 的关联上下文。"""
        self.base_url = base_url.rstrip("/")
        self.token_file = token_file
        self.context = context

    def _headers(
        self,
        confirmation_token: str | None = None,
        interaction_id: str | None = None,
    ) -> dict[str, str]:
        """生成内部 Token、用户和 trace/request/run/thread 请求头。"""
        token = Path(self.token_file).read_text(encoding="utf-8").strip()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Internal-User-Id": self.context.user_id,
            "X-Internal-User-Role": self.context.role,
            "X-Trace-Id": self.context.trace_id,
            "X-Request-Id": self.context.request_id,
            "X-Run-Id": self.context.run_id,
            "X-Thread-Id": self.context.thread_id,
            "X-Conversation-Id": self.context.conversation_id,
        }
        if confirmation_token:
            headers["X-Confirmation-Token"] = confirmation_token
        if interaction_id:
            headers["X-HITL-Interaction-Id"] = interaction_id
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        confirmation_token: str | None = None,
        interaction_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """发送受保护请求并将外部错误收敛为 Agent 可理解的异常。"""
        headers = self._headers(confirmation_token, interaction_id)
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=15.0) as client:
                response = await client.request(
                    method, path, headers=headers, params=params, json=body
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError, OSError) as error:
            raise TravelManageApiError("travel_api_unavailable") from error
        if not isinstance(data, dict):
            raise TravelManageApiError("travel_api_response_invalid")
        return data
