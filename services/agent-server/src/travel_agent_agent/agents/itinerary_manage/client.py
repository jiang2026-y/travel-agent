# 文件职责：封装 ItineraryManageAgent 到 API Server 内部差旅接口的安全调用。
# 定义 TravelManageApiClient，并统一透传用户身份、确认凭证和四类关联标识。
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from travel_agent_agent.agents.base import AgentContext


class TravelManageApiError(RuntimeError):
    """表示内部差旅 API 不可用或拒绝了请求，携带可展示的稳定错误码。"""

    def __init__(
        self, code: str, *, status_code: int | None = None, retryable: bool = False
    ) -> None:
        """保存错误码、上游状态与可重试标记，不保存响应正文。"""
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


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
        except (httpx.HTTPError, OSError) as error:
            raise TravelManageApiError("travel_api_unavailable", retryable=True) from error
        if response.status_code >= 400:
            raise TravelManageApiError(
                _extract_error_code(response), status_code=response.status_code
            )
        try:
            data = response.json()
        except ValueError as error:
            raise TravelManageApiError("travel_api_response_invalid") from error
        if not isinstance(data, dict):
            raise TravelManageApiError("travel_api_response_invalid")
        return data


def _extract_error_code(response: httpx.Response) -> str:
    """从内部错误信封读取稳定错误码，读取失败时回退到通用服务错误码。"""
    try:
        body = response.json()
    except ValueError:
        return "travel_api_unavailable"
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if isinstance(code, str) and code:
        return code[:64]
    return "travel_api_unavailable"
