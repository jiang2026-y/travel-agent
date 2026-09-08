# 本文件实现 Agent 到受限 Tool Gateway 的百炼 L2/L3/问题改写适配器。
# 定义 DashScopeGatewayError、DashScopeEmbeddingClient、DashScopeL3Client 和 DashScopeRewriteClient，
# 分别负责安全失败、1024 维向量、严格 JSON 意图识别和一次问题改写。
from __future__ import annotations

import json
from pathlib import Path

import httpx
from travel_agent_sensitive_masker import SensitiveMasker

from travel_agent_agent.core.correlation import get_active_correlation_context
from travel_agent_agent.core.json_utils import parse_model_json
from travel_agent_agent.core.prompt_loader import load_prompt
from travel_agent_agent.core.settings import Settings

_MINIMUM_TOKEN_LENGTH = 32
_LLM_READ_TIMEOUT_SECONDS = 65.0


class DashScopeGatewayError(RuntimeError):
    """表示受限 Tool Gateway 或上游只读推理能力不可用，且不暴露请求正文。"""


class _ToolGatewayClient:
    """封装 Agent 到 Tool Gateway 的认证、超时、脱敏和响应 JSON 校验。"""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """保存内部网关地址、Agent 专用 Secret 路径和可注入测试传输层。"""
        self._base_url = settings.tool_gateway_base_url
        self._token_file = settings.agent_gateway_token_file
        self._transport = transport
        self._masker = SensitiveMasker()

    async def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        """只向固定内部网关发送脱敏后的 JSON，并透传四类关联标识。"""
        token = _read_secret(self._token_file)
        context = get_active_correlation_context()
        headers = {"Authorization": f"Bearer {token}"}
        if context is not None:
            headers.update(context.request_headers())
        try:
            is_llm = path.endswith("chat-completions")
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(
                    70.0 if is_llm else 15.0,
                    connect=3.0,
                    read=_LLM_READ_TIMEOUT_SECONDS if is_llm else 12.0,
                ),
                transport=self._transport,
            ) as client:
                response = await client.post(path, headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise DashScopeGatewayError("dashscope_gateway_unavailable") from error
        if not isinstance(body, dict):
            raise DashScopeGatewayError("dashscope_gateway_response_invalid")
        return body

    def mask(self, text: str) -> str:
        """对发送给模型的当前消息和历史摘要执行同一套不可逆脱敏。"""
        return self._masker.mask_text(text)


class DashScopeEmbeddingClient:
    """实现 L2 EmbeddingPort，只允许 text-embedding-v4 的 1024 维输出。"""

    def __init__(self, gateway: _ToolGatewayClient) -> None:
        """接收内部受限网关客户端。"""
        self._gateway = gateway

    async def embed(self, text: str) -> list[float]:
        """请求单文本 embedding，并复用受限批量端点的首个向量。"""
        return (await self.embed_many([text]))[0]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """以最多十条文本批量请求 1024 维 embedding，并按 Provider index 恢复输入顺序。"""
        if not texts or len(texts) > 10:
            raise DashScopeGatewayError("dashscope_embedding_batch_size_invalid")
        body = await self._gateway.post(
            "/internal/v1/dashscope/embeddings",
            {
                "texts": [self._gateway.mask(text) for text in texts],
                "model": "text-embedding-v4",
                "dimensions": 1024,
            },
        )
        try:
            data = body["data"]
        except KeyError as error:
            raise DashScopeGatewayError("dashscope_embedding_response_invalid") from error
        if not isinstance(data, list) or len(data) != len(texts):
            raise DashScopeGatewayError("dashscope_embedding_batch_response_invalid")
        indexed_vectors: dict[int, list[float]] = {}
        for item in data:
            if not isinstance(item, dict):
                raise DashScopeGatewayError("dashscope_embedding_response_invalid")
            index = item.get("index")
            vector = item.get("embedding")
            if not isinstance(index, int) or isinstance(index, bool) or index not in range(len(texts)):
                raise DashScopeGatewayError("dashscope_embedding_index_invalid")
            if not isinstance(vector, list) or len(vector) != 1024:
                raise DashScopeGatewayError("dashscope_embedding_dimension_invalid")
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in vector):
                raise DashScopeGatewayError("dashscope_embedding_value_invalid")
            indexed_vectors[index] = [float(value) for value in vector]
        if len(indexed_vectors) != len(texts):
            raise DashScopeGatewayError("dashscope_embedding_index_invalid")
        return [indexed_vectors[index] for index in range(len(texts))]


class DashScopeL3Client:
    """实现 L3Port，使用 glm-5.1 生成严格 JSON 意图结果。"""

    def __init__(self, gateway: _ToolGatewayClient) -> None:
        """接收内部受限网关客户端。"""
        self._gateway = gateway

    async def classify(self, rewritten_question: str, history: str) -> str:
        """发送脱敏的当前消息和当前会话摘要，并只返回模型 content 字符串。"""
        return await self._chat(
            load_prompt("prompts/intent/l3_intent_recognition.md"),
            "当前问题：\n"
            f"{self._gateway.mask(rewritten_question)}\n\n"
            "当前会话脱敏摘要：\n"
            f"{self._gateway.mask(history)}",
        )

    async def _chat(self, system: str, user: str) -> str:
        """调用固定聊天端点并提取兼容接口的首个 assistant 文本。"""
        body = await self._gateway.post(
            "/internal/v1/dashscope/chat-completions",
            {
                "model": "glm-5.1",
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        try:
            content = body["choices"][0]["message"]["content"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError) as error:
            raise DashScopeGatewayError("dashscope_chat_response_invalid") from error
        if not isinstance(content, str) or not content.strip():
            raise DashScopeGatewayError("dashscope_chat_content_invalid")
        return content.strip()


class DashScopeRewriteClient(DashScopeL3Client):
    """实现 RewritePort，使用同一受限 glm-5.1 调用仅重写一次问题。"""

    async def rewrite(self, text: str, history: str) -> str:
        """返回单句改写；模型附带 JSON 或异常内容时安全交由后续 L3 兜底。"""
        result = await self._chat(
            load_prompt("prompts/intent/query_rewrite.md"),
            "当前问题：\n"
            f"{self._gateway.mask(text)}\n\n"
            "当前会话脱敏摘要：\n"
            f"{self._gateway.mask(history)}",
        )
        try:
            payload = parse_model_json(result)
            result = payload["rewritten_question"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise DashScopeGatewayError("dashscope_rewrite_response_invalid") from error
        if not isinstance(result, str) or not result.strip():
            raise DashScopeGatewayError("dashscope_rewrite_content_invalid")
        if len(result) > 8000:
            raise DashScopeGatewayError("dashscope_rewrite_too_long")
        return result.strip()


def create_dashscope_clients(settings: Settings) -> tuple[
    DashScopeEmbeddingClient, DashScopeL3Client, DashScopeRewriteClient
]:
    """集中创建共享安全边界的三类 Provider 端口，禁止 Agent 直连外网。"""
    settings.require_readonly_provider()
    gateway = _ToolGatewayClient(settings)
    return DashScopeEmbeddingClient(gateway), DashScopeL3Client(gateway), DashScopeRewriteClient(gateway)


def _read_secret(path: str) -> str:
    """从 Agent 专用 Docker Secret 读取网关认证 Token，禁止环境变量承载。"""
    try:
        token = Path(path).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise DashScopeGatewayError("agent_gateway_secret_unavailable") from error
    if len(token) < _MINIMUM_TOKEN_LENGTH:
        raise DashScopeGatewayError("agent_gateway_secret_invalid")
    return token
