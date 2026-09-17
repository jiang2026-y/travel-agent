# 本文件验证 Agent 的百炼批量 embedding 适配器。
# 定义 test_embedding_client_batches_and_reorders_results，
# 用于验证最多十条请求、敏感信息脱敏和按 index 还原向量。
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from travel_agent_agent.core.settings import Settings
from travel_agent_agent.infrastructure.dashscope_client import (
    DashScopeEmbeddingClient,
    DashScopeGatewayError,
    DashScopeRewriteClient,
    _ToolGatewayClient,
)


def test_embedding_client_batches_and_reorders_results(tmp_path) -> None:
    """批量客户端必须传递脱敏文本，且不能依赖 Provider 返回顺序。"""
    token_file = tmp_path / "agent_gateway_internal_token"
    token_file.write_text("g" * 32, encoding="utf-8")
    settings = Settings.from_environment(
        {
            "TRAVEL_AGENT_ENV": "development",
            "TRAVEL_AGENT_EXTERNAL_MODE": "real_readonly",
            "AGENT_GATEWAY_INTERNAL_TOKEN_FILE": str(token_file),
        }
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        """模拟网关乱序返回，断言 Agent 未把手机号原样送出服务边界。"""
        assert request.url.path == "/internal/v1/dashscope/embeddings"
        assert request.headers["authorization"] == f"Bearer {'g' * 32}"
        assert json.loads(request.content) == {
            "texts": ["138****5678", "查询航班"],
            "model": "text-embedding-v4",
            "dimensions": 1024,
        }
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [2.0] + [0.0] * 1023},
                    {"index": 0, "embedding": [1.0] + [0.0] * 1023},
                ]
            },
        )

    gateway = _ToolGatewayClient(settings, transport=httpx.MockTransport(handler))
    vectors = asyncio.run(DashScopeEmbeddingClient(gateway).embed_many(["13812345678", "查询航班"]))

    assert vectors[0][0] == 1.0
    assert vectors[1][0] == 2.0


async def _fenced_rewrite_gateway_response() -> dict[str, object]:
    """返回代码块包裹的问题改写响应。"""
    return {
        "choices": [{
            "message": {
                "content": (
                    '```json\n{"related": false, "rewritten_question": "请查询北京航班",'
                    ' "reason": "保持原意"}\n```'
                )
            }
        }]
    }


class _RewriteGateway:
    """隔离网络请求的最小问题改写网关替身。"""

    async def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        """返回固定代码块 JSON。"""
        del path, payload
        return await _fenced_rewrite_gateway_response()

    def mask(self, text: str) -> str:
        """返回测试文本。"""
        return text


@pytest.mark.asyncio
async def test_rewrite_client_extracts_question_from_fenced_json() -> None:
    """问题改写客户端应提取代码块 JSON 中的 rewritten_question。"""
    result = await DashScopeRewriteClient(_RewriteGateway()).rewrite("查航班", "")  # type: ignore[arg-type]

    assert result == "请查询北京航班"


@pytest.mark.asyncio
async def test_gateway_client_preserves_safe_quota_error(tmp_path) -> None:
    """Agent 网关客户端应保留稳定额度错误码，不降级成不可操作的泛化异常。"""
    token_file = tmp_path / "agent_gateway_internal_token"
    token_file.write_text("g" * 32, encoding="utf-8")
    settings = Settings.from_environment(
        {
            "TRAVEL_AGENT_ENV": "development",
            "TRAVEL_AGENT_EXTERNAL_MODE": "real_readonly",
            "AGENT_GATEWAY_INTERNAL_TOKEN_FILE": str(token_file),
        }
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        """模拟 Tool Gateway 已完成脱敏的额度错误。"""
        del request
        return httpx.Response(
            429,
            json={
                "error": {
                    "message": "百炼模型额度已耗尽，请恢复额度后重试。",
                    "type": "dashscope_quota_exhausted",
                    "code": "dashscope_quota_exhausted",
                    "retryable": False,
                }
            },
        )

    gateway = _ToolGatewayClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(DashScopeGatewayError) as exc_info:
        await gateway.post("/internal/v1/dashscope/chat-completions", {"value": "safe"})

    assert exc_info.value.code == "dashscope_quota_exhausted"
    assert exc_info.value.retryable is False
