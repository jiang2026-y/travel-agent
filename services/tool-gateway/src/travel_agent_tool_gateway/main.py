# 本文件装配受限 Tool Gateway 的百炼只读推理接口。
# 定义 GatewaySettings、create_app 和两个内部转发端点，分别负责安全配置、应用装配、
# embedding 与聊天推理的认证、字段约束和白名单转发。
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from travel_agent_tool_gateway.http_client import RestrictedHttpClient
from travel_agent_tool_gateway.observability import install_observability_middleware
from travel_agent_tool_gateway.policy import OperationDefinition, ProviderDefinition, ProviderRegistry

_DASHSCOPE_HOST = "ws-afyh9lpghkjx1iz8.cn-beijing.maas.aliyuncs.com"
_DASHSCOPE_BASE_URL = f"https://{_DASHSCOPE_HOST}/compatible-mode/v1"
_MINIMUM_TOKEN_LENGTH = 32
_LLM_READ_TIMEOUT_SECONDS = 60.0
_MASTER_TOOL_NAMES = frozenset({"info_query", "ask_user", "call_sub_agent"})
_MASTER_TOOL_NAMES = frozenset({"info_query", "ask_user", "call_sub_agent"})


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    """保存仅供网关使用的百炼地址、密钥 Secret 和 Agent 认证 Secret 路径。"""

    dashscope_base_url: str = _DASHSCOPE_BASE_URL
    dashscope_api_key_file: str = "/run/secrets/dashscope_api_key"
    agent_gateway_token_file: str = "/run/secrets/agent_gateway_internal_token"


class EmbeddingRequest(BaseModel):
    """约束 L2 embedding 请求，只允许已确认的模型、维度和最多十条文本输入。"""

    model_config = ConfigDict(extra="forbid")
    texts: tuple[str, ...] = Field(min_length=1, max_length=10)
    model: Literal["text-embedding-v4"] = "text-embedding-v4"
    dimensions: Literal[1024] = 1024


class ChatMessage(BaseModel):
    """约束 L3/改写的单条对话消息，不允许工具、图片或任意 Provider 参数。"""

    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = Field(default=None, max_length=10000)
    tool_call_id: str | None = Field(default=None, max_length=128)
    tool_calls: tuple[dict[str, object], ...] | None = Field(default=None, max_length=16)


class ToolDefinition(BaseModel):
    """约束 MasterAgent 可声明的标准工具描述。"""

    model_config = ConfigDict(extra="forbid")
    type: Literal["function"] = "function"
    function: dict[str, object]

    @property
    def name(self) -> str | None:
        """读取函数工具名称，供网关执行固定白名单校验。"""
        value = self.function.get("name")
        return value if isinstance(value, str) else None


class ChatRequest(BaseModel):
    """约束 L3/改写聊天请求，只允许 glm-5.1 和至多两条固定消息。"""

    model_config = ConfigDict(extra="forbid")
    model: Literal["glm-5.1", "qwen3.7-plus"] = "glm-5.1"
    messages: tuple[ChatMessage, ...] = Field(min_length=2, max_length=32)
    temperature: Literal[0] = 0
    enable_thinking: bool | None = None
    tools: tuple[ToolDefinition, ...] | None = Field(default=None, max_length=16)
    tool_choice: str | dict[str, object] | None = None


def _dashscope_registry(settings: GatewaySettings) -> ProviderRegistry:
    """登记唯一百炼工作空间与两个审查过的只读推理路径。"""
    return ProviderRegistry(
        [
            ProviderDefinition(
                provider_key="dashscope",
                base_url=settings.dashscope_base_url,
                allowed_hosts=(_DASHSCOPE_HOST,),
                operations=(
                    OperationDefinition("embedding", "POST", "/embeddings", True),
                    OperationDefinition("chat_completion", "POST", "/chat/completions", True),
                ),
            )
        ]
    )


def _read_secret(path: str) -> str:
    """从 Docker Secret 读取非空且足够长的凭据，绝不写入日志或响应。"""
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise HTTPException(status_code=503, detail="gateway_secret_unavailable") from error
    if len(value) < _MINIMUM_TOKEN_LENGTH:
        raise HTTPException(status_code=503, detail="gateway_secret_invalid")
    return value


async def _require_agent_gateway_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """验证 Agent 专用 Secret；浏览器和其他容器均不能调用推理转发端点。"""
    expected = _read_secret(request.app.state.gateway_settings.agent_gateway_token_file)
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="gateway_auth_failed")
    if not secrets.compare_digest(authorization.removeprefix("Bearer "), expected):
        raise HTTPException(status_code=401, detail="gateway_auth_failed")


def create_app(
    registry: ProviderRegistry | None = None,
    settings: GatewaySettings | None = None,
    client: RestrictedHttpClient | None = None,
) -> FastAPI:
    """创建仅可访问百炼只读推理能力的网关，禁止任意 URL、工具和写操作。"""
    gateway_settings = settings or GatewaySettings()
    provider_registry = registry or _dashscope_registry(gateway_settings)
    owns_client = client is None
    restricted_client = client or RestrictedHttpClient(provider_registry)
    application = FastAPI(title="Travel Agent Tool Gateway", version="0.1.0")
    application.state.gateway_settings = gateway_settings
    application.state.restricted_client = restricted_client
    install_observability_middleware(application)

    @application.get("/health", tags=["system"])
    async def health_check() -> dict[str, str | int]:
        """返回服务健康状态和已登记 Provider 数量，不触发外部网络访问。"""
        return {
            "service": "tool-gateway",
            "status": "ok",
            "external_capabilities": "dashscope_readonly_only",
            "provider_count": provider_registry.provider_count,
        }

    @application.post(
        "/internal/v1/dashscope/embeddings",
        dependencies=[Depends(_require_agent_gateway_token)],
        include_in_schema=False,
    )
    async def embeddings(payload: EmbeddingRequest) -> object:
        """转发固定 embedding 请求；只读推理正文不在网关日志中记录。"""
        return await _forward_json(
            restricted_client,
            "embedding",
            {"model": payload.model, "input": list(payload.texts), "dimensions": payload.dimensions},
            gateway_settings,
        )

    @application.post(
        "/internal/v1/dashscope/chat-completions",
        dependencies=[Depends(_require_agent_gateway_token)],
        include_in_schema=False,
    )
    @application.post(
        "/internal/v1/dashscope/chat/completions",
        dependencies=[Depends(_require_agent_gateway_token)],
        include_in_schema=False,
    )
    async def chat_completions(payload: ChatRequest) -> object:
        """转发固定 glm-5.1 聊天推理，不暴露任意模型、工具或写入参数。"""
        if payload.model == "glm-5.1" and (
            payload.enable_thinking is not None or payload.tools is not None
        ):
            raise HTTPException(status_code=422, detail="l3_tools_not_allowed")
        if payload.model == "qwen3.7-plus" and payload.enable_thinking is not False:
            raise HTTPException(status_code=422, detail="master_thinking_must_be_disabled")
        if payload.tools is not None and (
            payload.model != "qwen3.7-plus"
            or any(tool.name not in _MASTER_TOOL_NAMES for tool in payload.tools)
        ):
            raise HTTPException(status_code=422, detail="master_tool_not_allowed")
        return await _forward_json(
            restricted_client,
            "chat_completion",
            {
                "model": payload.model,
                "messages": [item.model_dump(exclude_none=True) for item in payload.messages],
                "temperature": payload.temperature,
                **({"enable_thinking": payload.enable_thinking} if payload.enable_thinking is not None else {}),
                **({"tools": [item.model_dump() for item in payload.tools]} if payload.tools is not None else {}),
                **({"tool_choice": payload.tool_choice} if payload.tool_choice is not None else {}),
            },
            gateway_settings,
        )

    if owns_client:

        @application.on_event("shutdown")
        async def close_restricted_client() -> None:
            """在网关停止时关闭唯一 HTTP 连接池。"""
            await restricted_client.aclose()

    return application


async def _forward_json(
    client: RestrictedHttpClient,
    operation_key: str,
    payload: dict[str, object],
    settings: GatewaySettings,
) -> object:
    """使用仅网关可读的百炼密钥转发 JSON，并把网络/上游异常收敛为安全错误。"""
    try:
        response = await client.execute_json(
            "dashscope",
            operation_key,
            payload,
            {"Authorization": f"Bearer {_read_secret(settings.dashscope_api_key_file)}"},
            read_timeout=(
                _LLM_READ_TIMEOUT_SECONDS if operation_key == "chat_completion" else None
            ),
        )
        return json.loads(response.content)
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, RuntimeError) as error:
        raise HTTPException(status_code=502, detail="dashscope_readonly_call_failed") from error


app = create_app()
