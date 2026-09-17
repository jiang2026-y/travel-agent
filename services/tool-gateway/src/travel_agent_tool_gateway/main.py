# 本文件装配受限 Tool Gateway 的百炼只读推理接口。
# 定义 GatewaySettings、create_app 和两个内部转发端点，分别负责安全配置、应用装配、
# embedding 与聊天推理的认证、字段约束和白名单转发。
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import StreamingResponse

from travel_agent_tool_gateway.http_client import RestrictedHttpClient
from travel_agent_tool_gateway.observability import install_observability_middleware
from travel_agent_tool_gateway.policy import (
    OperationDefinition,
    ProviderDefinition,
    ProviderRegistry,
    ToolPolicyDenied,
)

_DASHSCOPE_HOST = "ws-afyh9lpghkjx1iz8.cn-beijing.maas.aliyuncs.com"
_DASHSCOPE_BASE_URL = f"https://{_DASHSCOPE_HOST}/compatible-mode/v1"
_MINIMUM_TOKEN_LENGTH = 32
_LLM_READ_TIMEOUT_SECONDS = 60.0
_MAX_CHAT_MESSAGES = 128
# 单个 Agent 的工具集随技能与业务工具增长，白名单按 Agent 精确校验，这里只设安全上限。
_MAX_CHAT_TOOLS = 40
_LOGGER = logging.getLogger("travel_agent_tool_gateway.validation")
_WEATHER_HOST = "wttr.in"
_NEWSDATA_HOST = "newsdata.io"
_MEMORY_HOST = "dashscope.aliyuncs.com"
_VISA_HOST = "visa.orizn.app"
_VISA_BASE_PATH = "/api/v1"
_VISA_REFERER = "https://visa.orizn.app/mcp"
_VISA_TIMEOUT_SECONDS = 20.0
_VISA_KEYLESS_TOOLS = frozenset({"quick_check", "coverage"})
_VISA_TOOLS = frozenset(
    {"quick_check", "requirement", "transit", "compare", "changes", "coverage"}
)
_MEMORY_ADD_PATH = "/api/v2/apps/memory/add"
_MEMORY_SEARCH_PATH = "/api/v2/apps/memory/memory_nodes/search"
_MEMORY_LIST_PATH = "/api/v2/apps/memory/memory_nodes"
_MEMORY_PAGE_SIZE = 20
_MEMORY_TEXT_LIMIT = 4000
_MEMORY_METADATA_SOURCE = "travel-agent"
_SUPPORTED_MODELS_BY_AGENT = {
    "masterAgent": "qwen3.7-plus",
    "bookingAgent": "qwen3.7-plus",
    "itineraryManageAgent": "qwen3.7-plus",
    "infoAgent": "glm-5.1",
}
_AGENT_TOOL_NAMES = {
    "masterAgent": frozenset(
        {
            "ask_user",
            "itinerary_manage_agent",
            "booking_agent",
            "info_agent",
            "record_to_memory",
            "retrieve_from_memory",
        }
    ),
    "bookingAgent": frozenset(
        {
            "query_travel_order",
            "query_travel_order_by_order_id",
            "query_travel_orders",
            "query_approval_status",
            "check_travel_order_approval",
            "check_travel_time_validity",
            "query_booking_record",
            "query_user_contact_info",
            "update_user_contact_info",
            "query_user_base_location",
            "update_user_base_location",
            "check_tuniu_api_key",
            "save_tuniu_api_key",
            "check_flight_api_key",
            "save_flight_api_key",
            "load_skill_through_path",
            "execute_shell_command",
            "record_to_memory",
            "retrieve_from_memory",
            "search_tuniu_flight",
            "search_tuniu_train",
            "search_tuniu_hotel",
            "create_tuniu_flight_order",
            "create_tuniu_train_order",
            "create_tuniu_hotel_order",
            "cancel_booking",
        }
    ),
    "itineraryManageAgent": frozenset(
        {
            "submit_travel_approval",
            "cancel_travel_order",
            "modify_travel_order",
            "check_travel_order_conflicts",
            "query_travel_order",
            "query_travel_order_by_order_id",
            "query_travel_orders",
            "query_approval_status",
            "check_travel_order_approval",
            "check_travel_time_validity",
            "query_booking_record",
            "cancel_booking",
            "query_travel_policy",
            "check_travel_policy",
            "query_user_contact_info",
            "update_user_contact_info",
            "query_user_base_location",
            "update_user_base_location",
        }
    ),
    "infoAgent": frozenset(
        {
            "retrieve_knowledge",
            "query_travel_policy",
            "check_travel_policy",
            "query_weather",
            "query_destination_news",
            "quick_visa_check",
            "check_visa_requirement",
            "check_transit_visa",
            "compare_destinations",
            "get_recent_changes",
            "get_coverage_stats",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class _SafeDashScopeError:
    """保存可返回给内部调用方的稳定错误码，不携带百炼原始响应。"""

    code: str
    message: str
    status_code: int
    retryable: bool


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    """保存仅供网关使用的百炼地址、密钥 Secret 和 Agent 认证 Secret 路径。"""

    dashscope_base_url: str = _DASHSCOPE_BASE_URL
    dashscope_api_key_file: str = "/run/secrets/dashscope_api_key"
    agent_gateway_token_file: str = "/run/secrets/agent_gateway_internal_token"
    bailian_workspace_id: str = ""
    bailian_index_id: str = ""
    bailian_access_key_id_file: str = "/run/secrets/bailian_access_key_id"
    bailian_access_key_secret_file: str = "/run/secrets/bailian_access_key_secret"
    bailian_enabled: bool = False
    newsdata_api_key_file: str = "/run/secrets/newsdata_api_key"
    weather_mcp_endpoint_file: str = "/run/secrets/weather_mcp_endpoint"
    orizn_visa_api_key_file: str = "/run/secrets/orizn_visa_api_key"
    bailian_memory_library_id_file: str = "/run/secrets/bailian_memory_library_id"
    bailian_memory_project_id_file: str = "/run/secrets/bailian_project_id"
    bailian_memory_profile_schema_file: str = "/run/secrets/bailian_profile_schema"
    bailian_memory_api_base: str = "https://dashscope.aliyuncs.com"

    @classmethod
    def from_environment(cls) -> GatewaySettings:
        """从环境变量读取网关地址和百炼知识库配置，不读取密钥正文。"""
        return cls(
            dashscope_base_url=os.environ.get("DASHSCOPE_BASE_URL", _DASHSCOPE_BASE_URL),
            dashscope_api_key_file=os.environ.get(
                "DASHSCOPE_API_KEY_FILE", "/run/secrets/dashscope_api_key"
            ),
            agent_gateway_token_file=os.environ.get(
                "AGENT_GATEWAY_INTERNAL_TOKEN_FILE", "/run/secrets/agent_gateway_internal_token"
            ),
            bailian_workspace_id=os.environ.get("BAILIAN_WORKSPACE_ID", ""),
            bailian_index_id=os.environ.get("BAILIAN_INDEX_ID", ""),
            bailian_access_key_id_file=os.environ.get(
                "BAILIAN_ACCESS_KEY_ID_FILE", "/run/secrets/bailian_access_key_id"
            ),
            bailian_access_key_secret_file=os.environ.get(
                "BAILIAN_ACCESS_KEY_SECRET_FILE", "/run/secrets/bailian_access_key_secret"
            ),
            bailian_enabled=os.environ.get("BAILIAN_ENABLED", "false").strip().lower()
            in {"1", "true"},
            newsdata_api_key_file=os.environ.get(
                "NEWSDATA_API_KEY_FILE", "/run/secrets/newsdata_api_key"
            ),
            weather_mcp_endpoint_file=os.environ.get(
                "WEATHER_MCP_ENDPOINT_FILE", "/run/secrets/weather_mcp_endpoint"
            ),
            orizn_visa_api_key_file=os.environ.get(
                "ORIZN_VISA_API_KEY_FILE", "/run/secrets/orizn_visa_api_key"
            ),
            bailian_memory_library_id_file=os.environ.get(
                "BAILIAN_MEMORY_LIBRARY_ID_FILE", "/run/secrets/bailian_memory_library_id"
            ),
            bailian_memory_project_id_file=os.environ.get(
                "BAILIAN_MEMORY_PROJECT_ID_FILE", "/run/secrets/bailian_project_id"
            ),
            bailian_memory_profile_schema_file=os.environ.get(
                "BAILIAN_MEMORY_PROFILE_SCHEMA_FILE", "/run/secrets/bailian_profile_schema"
            ),
            bailian_memory_api_base=os.environ.get(
                "BAILIAN_MEMORY_API_BASE", "https://dashscope.aliyuncs.com"
            ),
        )


class KnowledgeRetrieveRequest(BaseModel):
    """约束知识库检索请求，仅允许信息 Agent 传入有限长度的查询文本。"""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)


class EmbeddingRequest(BaseModel):
    """约束 L2 embedding 请求，只允许已确认的模型、维度和最多十条文本输入。"""

    model_config = ConfigDict(extra="forbid")
    texts: tuple[str, ...] = Field(min_length=1, max_length=10)
    model: Literal["text-embedding-v4"] = "text-embedding-v4"
    dimensions: Literal[1024] = 1024


class WeatherQueryRequest(BaseModel):
    """约束目的地天气查询请求，只接受城市名与可选日期。"""

    model_config = ConfigDict(extra="forbid")
    city: str = Field(min_length=1, max_length=64)
    date: str | None = Field(default=None, max_length=10)


class DestinationNewsRequest(BaseModel):
    """约束目的地资讯查询请求，只接受城市名与可选主题。"""

    model_config = ConfigDict(extra="forbid")
    city: str = Field(min_length=1, max_length=64)
    topic: str | None = Field(default=None, max_length=32)


class MemoryRecordRequest(BaseModel):
    """约束长期记忆写入请求，只接受用户标识与脱敏后的偏好文本。"""

    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    content: str = Field(min_length=1, max_length=2000)


class MemoryRetrieveRequest(BaseModel):
    """约束长期记忆召回请求，只接受用户标识与受限查询文本。"""

    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    query: str = Field(min_length=1, max_length=1000)


class VisaQueryRequest(BaseModel):
    """约束签证查询请求；字段按 tool 在网关内逐项校验，不接受其它参数。"""

    model_config = ConfigDict(extra="forbid")
    passport: str | None = Field(default=None, max_length=3)
    destination: str | None = Field(default=None, max_length=3)
    transit_country: str | None = Field(default=None, max_length=3)
    destinations: tuple[str, ...] | None = Field(default=None, max_length=8)
    lang: str | None = Field(default=None, max_length=2)


class ChatMessage(BaseModel):
    """约束 L3/改写的单条对话消息，不允许工具、图片或任意 Provider 参数。"""

    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant", "tool"]
    # 工具消息会携带结构化查询结果（已在 Agent 侧压缩并限制条数），因此上限高于普通消息。
    content: str | None = Field(default=None, max_length=24000)
    # LangChain 会为部分消息带上 name 字段（如 HumanInTheLoop 注入的消息），属于协议合法字段。
    name: str | None = Field(default=None, max_length=128)
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
    """约束 L3/改写/推荐聊天请求，只允许已登记的三个只读模型。"""

    model_config = ConfigDict(extra="forbid")
    model: Literal["glm-5.1", "qwen3.7-plus", "qwen3.6-flash"] = "glm-5.1"
    messages: tuple[ChatMessage, ...] = Field(min_length=2, max_length=_MAX_CHAT_MESSAGES)
    # LangChain OpenAI 兼容客户端会在非流式请求中自动发送 stream=false。
    stream: bool | None = None
    temperature: Literal[0] = 0
    enable_thinking: bool | None = None
    tools: tuple[ToolDefinition, ...] | None = Field(default=None, max_length=_MAX_CHAT_TOOLS)
    tool_choice: str | dict[str, object] | None = None


def _dashscope_registry(settings: GatewaySettings) -> ProviderRegistry:
    """登记百炼推理、免费天气和目的地资讯三个审查过的只读访问路径。"""
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
            ),
            ProviderDefinition(
                provider_key="weather",
                base_url=f"https://{_WEATHER_HOST}",
                allowed_hosts=(_WEATHER_HOST,),
                operations=(
                    OperationDefinition(
                        "current", "GET", "/{city}", True, allowed_query_params=("format",)
                    ),
                ),
            ),
            ProviderDefinition(
                provider_key="newsdata",
                base_url=f"https://{_NEWSDATA_HOST}",
                allowed_hosts=(_NEWSDATA_HOST,),
                operations=(
                    OperationDefinition(
                        "search",
                        "GET",
                        "/api/1/news",
                        True,
                        allowed_query_params=("apikey", "q", "language", "size"),
                    ),
                ),
            ),
            ProviderDefinition(
                provider_key="bailian-memory",
                base_url=settings.bailian_memory_api_base,
                allowed_hosts=(_MEMORY_HOST,),
                operations=(
                    OperationDefinition("add_memory", "POST", _MEMORY_ADD_PATH, False),
                    OperationDefinition("search_memory", "POST", _MEMORY_SEARCH_PATH, True),
                    OperationDefinition(
                        "list_memory",
                        "GET",
                        _MEMORY_LIST_PATH,
                        True,
                        allowed_query_params=(
                            "user_id",
                            "page_size",
                            "page_num",
                            "memory_library_id",
                        ),
                    ),
                ),
                # 百炼记忆库写入是唯一被显式放行的外部写操作，路径与域名仍由注册表固定。
                allows_registered_writes=True,
            ),
            ProviderDefinition(
                provider_key="orizn-visa",
                base_url=f"https://{_VISA_HOST}{_VISA_BASE_PATH}",
                allowed_hosts=(_VISA_HOST,),
                operations=(
                    OperationDefinition(
                        "visa_detail",
                        "GET",
                        "/visa",
                        True,
                        allowed_query_params=("passport", "destination", "lang"),
                    ),
                    OperationDefinition(
                        "visa_check",
                        "GET",
                        "/visa/check",
                        True,
                        allowed_query_params=("passport", "destination", "lang"),
                    ),
                    OperationDefinition(
                        "visa_bulk",
                        "GET",
                        "/visa/bulk",
                        True,
                        allowed_query_params=("passport", "destination", "lang"),
                    ),
                    OperationDefinition(
                        "visa_changes",
                        "GET",
                        "/visa/changes",
                        True,
                        allowed_query_params=("passport", "destination", "lang"),
                    ),
                    OperationDefinition("visa_stats", "GET", "/visa/stats", True),
                ),
            ),
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
    gateway_settings = settings or GatewaySettings.from_environment()
    provider_registry = registry or _dashscope_registry(gateway_settings)
    owns_client = client is None
    restricted_client = client or RestrictedHttpClient(provider_registry)
    application = FastAPI(title="Travel Agent Tool Gateway", version="0.1.0")
    application.state.gateway_settings = gateway_settings
    application.state.restricted_client = restricted_client
    install_observability_middleware(application)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """记录校验失败的字段位置与原因（不含请求取值），响应体保持默认结构。"""
        _LOGGER.warning(
            "gateway_validation_failed path=%s details=%s",
            request.url.path,
            _safe_validation_details(exc.errors()),
        )
        return JSONResponse(
            status_code=422, content={"detail": jsonable_encoder(exc.errors())}
        )

    @application.get("/health", tags=["system"])
    async def health_check() -> dict[str, str | int]:
        """返回服务健康状态和已登记 Provider 数量，不触发外部网络访问。"""
        return {
            "service": "tool-gateway",
            "status": "ok",
            "external_capabilities": "readonly_providers_registered",
            "provider_count": provider_registry.provider_count,
        }

    @application.post(
        "/internal/v1/bailian/retrieve",
        dependencies=[Depends(_require_agent_gateway_token)],
        include_in_schema=False,
    )
    async def retrieve_knowledge(
        payload: KnowledgeRetrieveRequest,
        x_agent_name: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        """使用服务端 AccessKey 检索百炼知识库，只返回脱敏文本和来源摘要。"""
        if x_agent_name != "infoAgent":
            raise HTTPException(status_code=403, detail="knowledge_agent_not_allowed")
        if not gateway_settings.bailian_enabled or not _bailian_configured(gateway_settings):
            raise HTTPException(status_code=503, detail="bailian_knowledge_not_configured")
        return await _retrieve_bailian(gateway_settings, payload.query)

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
            {
                "model": payload.model,
                "input": list(payload.texts),
                "dimensions": payload.dimensions,
            },
            gateway_settings,
        )

    @application.post(
        "/internal/v1/destination/weather",
        dependencies=[Depends(_require_agent_gateway_token)],
        include_in_schema=False,
    )
    async def destination_weather(payload: WeatherQueryRequest) -> object:
        """查询目的地天气；超出免费预报范围时尝试更长期天气 MCP，未配置则降级。"""
        return await _query_weather(restricted_client, gateway_settings, payload.city, payload.date)

    @application.post(
        "/internal/v1/destination/news",
        dependencies=[Depends(_require_agent_gateway_token)],
        include_in_schema=False,
    )
    async def destination_news(payload: DestinationNewsRequest) -> object:
        """查询目的地资讯；未配置资讯 Key 时返回可展示的降级结果。"""
        return await _query_destination_news(
            restricted_client, gateway_settings, payload.city, payload.topic
        )

    @application.post(
        "/internal/v1/bailian/memory/record",
        dependencies=[Depends(_require_agent_gateway_token)],
        include_in_schema=False,
    )
    async def memory_record(payload: MemoryRecordRequest) -> object:
        """写入用户差旅偏好长期记忆；未配置记忆库时降级为未启用。"""
        return await _call_memory_api(
            restricted_client, gateway_settings, payload.user_id, "record", payload.content
        )

    @application.post(
        "/internal/v1/bailian/memory/retrieve",
        dependencies=[Depends(_require_agent_gateway_token)],
        include_in_schema=False,
    )
    async def memory_retrieve(payload: MemoryRetrieveRequest) -> object:
        """召回用户差旅偏好长期记忆；未配置记忆库时返回空召回。"""
        return await _call_memory_api(
            restricted_client, gateway_settings, payload.user_id, "retrieve", payload.query
        )

    @application.post(
        "/internal/v1/visa/{tool}",
        dependencies=[Depends(_require_agent_gateway_token)],
        include_in_schema=False,
    )
    async def visa_query(tool: str, payload: VisaQueryRequest) -> object:
        """按工具名查询 Orizn 签证数据；未配置 Key 时仅免 Key 工具可用。"""
        return await _query_visa(restricted_client, gateway_settings, tool, payload)

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
    async def chat_completions(
        payload: ChatRequest,
        x_agent_name: Annotated[str | None, Header()] = None,
    ) -> object:
        """按模型与 Agent 名称转发聊天推理，工具只放行该 Agent 已登记的白名单。"""
        _validate_chat_request(payload, x_agent_name)
        forwarded: dict[str, object] = {
            "model": payload.model,
            "messages": [item.model_dump(exclude_none=True) for item in payload.messages],
            **({"stream": payload.stream} if payload.stream is not None else {}),
            "temperature": payload.temperature,
            **(
                {"enable_thinking": payload.enable_thinking}
                if payload.enable_thinking is not None
                else {}
            ),
            **(
                {"tools": [item.model_dump() for item in payload.tools]}
                if payload.tools is not None
                else {}
            ),
            **({"tool_choice": payload.tool_choice} if payload.tool_choice is not None else {}),
        }
        if payload.stream:
            return await _forward_stream(
                restricted_client, "chat_completion", forwarded, gateway_settings
            )
        return await _forward_json(
            restricted_client, "chat_completion", forwarded, gateway_settings
        )

    if owns_client:

        @application.on_event("shutdown")
        async def close_restricted_client() -> None:
            """在网关停止时关闭唯一 HTTP 连接池。"""
            await restricted_client.aclose()

    return application


async def _forward_stream(
    client: RestrictedHttpClient,
    operation_key: str,
    payload: dict[str, object],
    settings: GatewaySettings,
) -> object:
    """以 SSE 原样转发上游流式推理；不缓存正文，也不写入日志。"""
    headers = {"Authorization": f"Bearer {_read_secret(settings.dashscope_api_key_file)}"}
    stream = client.stream_json(
        "dashscope",
        operation_key,
        payload,
        headers,
        read_timeout=_LLM_READ_TIMEOUT_SECONDS,
    )
    try:
        # 先取第一块，确保上游的鉴权/额度类错误仍能按安全错误信封返回。
        first = await anext(stream)
    except StopAsyncIteration:
        return JSONResponse(
            status_code=200, content={"choices": [], "finish_reason": "empty_stream"}
        )
    except httpx.HTTPStatusError as error:
        return _safe_dashscope_error_response(_map_dashscope_status_error(error))
    except (httpx.HTTPError, ValueError, RuntimeError):
        return _safe_dashscope_error_response(_dashscope_upstream_unavailable())

    async def body() -> AsyncIterator[bytes]:
        """产出首块与后续所有块；上游中断时直接结束连接。"""
        yield first
        try:
            async for chunk in stream:
                yield chunk
        except (httpx.HTTPError, RuntimeError):
            return

    return StreamingResponse(body(), media_type="text/event-stream")
def _safe_dashscope_error_response(error: _SafeDashScopeError) -> JSONResponse:
    """把百炼安全错误转换为网关统一错误信封。"""
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                "message": error.message,
                "type": error.code,
                "code": error.code,
                "retryable": error.retryable,
            }
        },
    )


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
    except httpx.HTTPStatusError as error:
        safe_error = _map_dashscope_status_error(error)
        return JSONResponse(
            status_code=safe_error.status_code,
            content={
                "error": {
                    "message": safe_error.message,
                    "type": safe_error.code,
                    "code": safe_error.code,
                    "retryable": safe_error.retryable,
                }
            },
        )
    except (httpx.HTTPError, ValueError, RuntimeError):
        safe_error = _dashscope_upstream_unavailable()
        return JSONResponse(
            status_code=safe_error.status_code,
            content={
                "error": {
                    "message": safe_error.message,
                    "type": safe_error.code,
                    "code": safe_error.code,
                    "retryable": safe_error.retryable,
                }
            },
        )


def _map_dashscope_status_error(error: httpx.HTTPStatusError) -> _SafeDashScopeError:
    """仅读取上游错误码和 HTTP 状态，将其映射为不泄漏响应正文的稳定错误。"""
    upstream_code = ""
    try:
        body = error.response.json()
        upstream_error = body.get("error") if isinstance(body, dict) else None
        if isinstance(upstream_error, dict):
            raw_code = upstream_error.get("code") or upstream_error.get("type")
            if isinstance(raw_code, str):
                upstream_code = raw_code.strip().lower()
    except ValueError:
        pass

    status_code = error.response.status_code
    if upstream_code in {
        "insufficient_quota",
        "quota_exhausted",
        "free_quota_exhausted",
    }:
        return _SafeDashScopeError(
            code="dashscope_quota_exhausted",
            message="百炼模型额度已耗尽，请恢复额度后重试。",
            status_code=429,
            retryable=False,
        )
    if status_code == 401 or upstream_code in {
        "authentication_error",
        "invalid_api_key",
        "unauthorized",
    }:
        return _SafeDashScopeError(
            code="dashscope_auth_failed",
            message="百炼服务认证失败，请检查服务端密钥。",
            status_code=502,
            retryable=False,
        )
    if status_code in {403, 404} or upstream_code in {
        "model_not_found",
        "model_access_denied",
        "permission_denied",
        "access_denied",
    }:
        return _SafeDashScopeError(
            code="dashscope_model_unavailable",
            message="当前模型不存在或服务端账号无权访问。",
            status_code=502,
            retryable=False,
        )
    return _dashscope_upstream_unavailable()


def _dashscope_upstream_unavailable() -> _SafeDashScopeError:
    """生成网络、超时和未知上游故障共用的安全错误。"""
    return _SafeDashScopeError(
        code="dashscope_upstream_unavailable",
        message="百炼上游服务暂不可用，请稍后重试。",
        status_code=503,
        retryable=True,
    )


def _validate_chat_request(payload: ChatRequest, agent_name: str | None) -> None:
    """校验模型、思考模式与 Agent 工具白名单，拒绝越权或不一致组合。"""
    if payload.tools is None:
        if payload.model == "glm-5.1" and payload.enable_thinking is not None:
            raise HTTPException(status_code=422, detail="l3_tools_not_allowed")
        if payload.model == "qwen3.7-plus" and payload.enable_thinking is not False:
            raise HTTPException(status_code=422, detail="master_thinking_must_be_disabled")
        if payload.model == "qwen3.6-flash" and payload.enable_thinking is not False:
            raise HTTPException(
                status_code=422, detail="recommendation_tools_or_thinking_not_allowed"
            )
        return
    expected_model = _SUPPORTED_MODELS_BY_AGENT.get(agent_name or "")
    if expected_model is None:
        raise HTTPException(status_code=422, detail="agent_name_required_for_tools")
    if payload.model != expected_model:
        raise HTTPException(status_code=422, detail="agent_model_mismatch")
    if payload.enable_thinking is not False:
        raise HTTPException(status_code=422, detail="agent_thinking_must_be_disabled")
    allowed = _AGENT_TOOL_NAMES.get(agent_name or "", frozenset())
    if any(tool.name not in allowed for tool in payload.tools):
        raise HTTPException(status_code=422, detail="agent_tool_not_allowed")


def _read_optional_secret(path: str, minimum_length: int) -> str | None:
    """读取可选 Secret；缺失、不可读或过短时返回 None 而不中断服务。"""
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if len(value) < minimum_length:
        return None
    return value


def _safe_validation_details(errors: object) -> list[dict[str, object]]:
    """只提取校验错误的类型、字段位置与说明，绝不记录请求取值。"""
    if not isinstance(errors, list):
        return []
    safe: list[dict[str, object]] = []
    for item in errors[:8]:
        if not isinstance(item, dict):
            continue
        location = item.get("loc")
        safe.append(
            {
                "type": str(item.get("type"))[:64],
                "loc": [str(part)[:48] for part in location]
                if isinstance(location, (list, tuple))
                else [],
                "msg": str(item.get("msg"))[:200],
            }
        )
    return safe


async def _query_weather(
    client: RestrictedHttpClient,
    settings: GatewaySettings,
    city: str,
    date_text: str | None,
) -> dict[str, object]:
    """查询目的地天气；超出免费范围时尝试更长期天气 MCP，未配置则降级。"""
    today = date.today()
    target = _parse_iso_date(date_text or "")
    if target is not None and target > today + timedelta(days=2):
        mcp_result = await _query_weather_mcp(settings, city, date_text or "")
        if mcp_result is not None:
            return mcp_result
        return {
            "city": city,
            "date": date_text,
            "beyond_range": True,
            "available": False,
            "message": "该日期超出免费天气预报范围，当前未启用更长期天气服务。",
        }
    try:
        response = await client.execute_get(
            "weather", "current", path_params={"city": city}, query_params={"format": "j1"}
        )
        raw = json.loads(response.content.decode("utf-8"))
    except (ToolPolicyDenied, httpx.HTTPError, ValueError, RuntimeError, UnicodeDecodeError):
        return {"city": city, "available": False, "message": "天气查询暂时不可用，请稍后重试。"}
    return _normalize_weather(city, date_text, target, raw, today)


def _normalize_weather(
    city: str,
    date_text: str | None,
    target: date | None,
    raw: object,
    today: date,
) -> dict[str, object]:
    """将外部天气报文裁剪为当前实况与逐日预报摘要。"""
    if not isinstance(raw, dict):
        return {"city": city, "available": False, "message": "天气数据解析失败。"}
    result: dict[str, object] = {
        "city": city,
        "source": "wttr.in",
        "queryDate": date_text or "today",
        "available": True,
    }
    if target is None or target == today:
        current_condition = raw.get("current_condition")
        if isinstance(current_condition, list) and current_condition:
            current = current_condition[0]
            if isinstance(current, dict):
                result["current"] = {
                    "tempC": f"{current.get('temp_C')}°C",
                    "feelsLikeC": f"{current.get('FeelsLikeC')}°C",
                    "humidity": f"{current.get('humidity')}%",
                    "windKmph": f"{current.get('windspeedKmph')} km/h",
                    "description": _first_weather_desc(current),
                }
    forecast_days = raw.get("weather")
    forecast: list[dict[str, object]] = []
    if isinstance(forecast_days, list):
        for day in forecast_days:
            if not isinstance(day, dict):
                continue
            day_date = _parse_iso_date(str(day.get("date") or ""))
            if target is not None and target != today and day_date != target:
                continue
            slots: list[dict[str, object]] = []
            hourly = day.get("hourly")
            if isinstance(hourly, list):
                for slot in hourly:
                    if not isinstance(slot, dict):
                        continue
                    slots.append(
                        {
                            "time": _format_hourly_time(slot.get("time")),
                            "tempC": f"{slot.get('tempC')}°C",
                            "windKmph": f"{slot.get('windspeedKmph')} km/h",
                            "chanceOfRain": f"{slot.get('chanceofrain')}%",
                            "description": _first_weather_desc(slot),
                        }
                    )
            forecast.append(
                {
                    "date": day.get("date"),
                    "maxTempC": f"{day.get('maxtempC')}°C",
                    "minTempC": f"{day.get('mintempC')}°C",
                    "hourly": slots,
                }
            )
    result["forecast"] = forecast
    if target is not None and target != today and not forecast:
        result["beyond_range"] = True
        result["message"] = "该日期超出免费天气预报范围。"
    return result


def _first_weather_desc(payload: dict[str, object]) -> str:
    """读取天气描述文本，缺失时返回空串。"""
    descriptions = payload.get("weatherDesc")
    if isinstance(descriptions, list) and descriptions:
        first = descriptions[0]
        if isinstance(first, dict) and isinstance(first.get("value"), str):
            return str(first["value"])
    return ""


def _format_hourly_time(value: object) -> str:
    """将 wttr.in 的 0~2100 小时字段转换为 HH:mm。"""
    try:
        hour = int(str(value))
    except (TypeError, ValueError):
        return ""
    return f"{hour // 100:02d}:{hour % 100:02d}"


async def _query_weather_mcp(
    settings: GatewaySettings, city: str, date_text: str
) -> dict[str, object] | None:
    """调用可选的更长期天气 MCP；未配置或调用失败时返回 None 表示降级。"""
    endpoint = _read_optional_secret(settings.weather_mcp_endpoint_file, 16)
    if not endpoint:
        return None
    payload = {
        "jsonrpc": "2.0",
        "id": "weather-1",
        "method": "tools/call",
        "params": {"name": "city_weather", "arguments": {"city": city, "date": date_text}},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers={"Accept": "application/json, text/event-stream"},
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    return {
        "city": city,
        "date": date_text,
        "source": "weather-mcp",
        "available": True,
        "result": json.dumps(body, ensure_ascii=False)[:4000],
    }


async def _query_destination_news(
    client: RestrictedHttpClient,
    settings: GatewaySettings,
    city: str,
    topic: str | None,
) -> dict[str, object]:
    """查询目的地资讯；未配置资讯 Key 时返回可展示的降级结果。"""
    api_key = _read_optional_secret(settings.newsdata_api_key_file, 8)
    if not api_key:
        return {
            "city": city,
            "topic": topic,
            "available": False,
            "message": "目的地资讯查询未启用，请建议用户查阅当地官方渠道。",
        }
    query = f"{city} {topic}".strip() if topic else city
    try:
        response = await client.execute_get(
            "newsdata",
            "search",
            query_params={"apikey": api_key, "q": query, "language": "zh", "size": "5"},
        )
        raw = json.loads(response.content.decode("utf-8"))
    except (ToolPolicyDenied, httpx.HTTPError, ValueError, RuntimeError, UnicodeDecodeError):
        return {
            "city": city,
            "topic": topic,
            "available": False,
            "message": "资讯查询失败，请稍后重试。",
        }
    results = raw.get("results") if isinstance(raw, dict) else None
    articles: list[dict[str, object]] = []
    if isinstance(results, list):
        for article in results[:5]:
            if not isinstance(article, dict):
                continue
            articles.append(
                {
                    "title": article.get("title"),
                    "description": article.get("description"),
                    "pubDate": article.get("pubDate"),
                    "source": article.get("source_id"),
                }
            )
    return {
        "city": city,
        "topic": topic,
        "source": "newsdata.io",
        "available": True,
        "news": articles,
        "count": len(articles),
    }


async def _call_memory_api(
    client: RestrictedHttpClient,
    settings: GatewaySettings,
    user_id: str,
    action: str,
    text: str,
) -> dict[str, object]:
    """按百炼记忆库 v2 契约读写长期记忆，未配置或上游失败时安全降级。"""
    api_key = _read_optional_secret(settings.dashscope_api_key_file, 16)
    library_id = _read_optional_secret(settings.bailian_memory_library_id_file, 4)
    if not api_key or not library_id:
        return {
            "available": False,
            "memories": "",
            "message": "长期记忆未启用，本轮未读写历史偏好。",
        }
    project_id = _read_optional_secret(settings.bailian_memory_project_id_file, 1)
    profile_schema = _read_optional_secret(settings.bailian_memory_profile_schema_file, 1)
    try:
        if action == "record":
            payload = _memory_payload(library_id, user_id, text, project_id, profile_schema)
            response = await client.execute_write_json(
                "bailian-memory",
                "add_memory",
                payload,
                {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                read_timeout=20.0,
            )
        else:
            # 记忆节点检索接口在当前账号下恒返回空，改用按用户列举节点的稳定路径。
            response = await client.execute_get(
                "bailian-memory",
                "list_memory",
                query_params={
                    "user_id": user_id,
                    "page_size": str(_MEMORY_PAGE_SIZE),
                    "page_num": "1",
                },
                headers={"Authorization": f"Bearer {api_key}"},
                read_timeout=20.0,
            )
        body = json.loads(response.content.decode("utf-8"))
    except (ToolPolicyDenied, httpx.HTTPError, ValueError, UnicodeDecodeError, KeyError):
        return {"available": False, "memories": "", "message": "长期记忆服务暂不可用。"}
    nodes = body.get("memory_nodes") if isinstance(body, dict) else None
    if action == "record":
        return {
            "available": True,
            "memories": "",
            "count": len(nodes) if isinstance(nodes, list) else 0,
            "message": "已记录该差旅偏好。",
        }
    return {
        "available": True,
        "memories": _extract_memory_text(nodes),
        "count": len(nodes) if isinstance(nodes, list) else 0,
        "message": "已返回长期记忆召回结果。",
    }


def _memory_payload(
    library_id: str,
    user_id: str,
    text: str,
    project_id: str | None,
    profile_schema: str | None,
) -> dict[str, object]:
    """按百炼记忆库 v2 契约构造偏好写入请求体。"""
    payload: dict[str, object] = {
        "memory_library_id": library_id,
        "user_id": user_id,
        "messages": [{"role": "user", "content": text}],
    }
    # 字段名与官方 agentscope-runtime / AgentScope Java 契约一致，均为 meta_data。
    payload["meta_data"] = {"source": _MEMORY_METADATA_SOURCE}
    if project_id:
        payload["project_id"] = project_id
    if profile_schema:
        payload["profile_schema"] = profile_schema
    return payload


def _extract_memory_text(nodes: object) -> str:
    """拼接记忆节点正文，无法识别时返回空串。"""
    if not isinstance(nodes, list):
        return ""
    texts: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("status") not in (None, "valid"):
            continue
        content = node.get("content")
        if isinstance(content, str) and content.strip():
            texts.append(content.strip())
    return "\n".join(texts)[:_MEMORY_TEXT_LIMIT]


async def _query_visa(
    client: RestrictedHttpClient,
    settings: GatewaySettings,
    tool: str,
    payload: VisaQueryRequest,
) -> dict[str, object]:
    """按工具名调用 Orizn 签证 REST 接口，未配置 Key 或上游失败时安全降级。"""
    operation_key, query_params = _visa_request(tool, payload)
    api_key = _read_optional_secret(settings.orizn_visa_api_key_file, 8)
    if api_key is None and tool not in _VISA_KEYLESS_TOOLS:
        return {
            "available": False,
            "error_code": "orizn_visa_key_required",
            "message": "签证服务的访问密钥尚未配置，请联系管理员。",
        }
    headers = {"Referer": _VISA_REFERER}
    if api_key is not None:
        headers["x-api-key"] = api_key
    try:
        response = await client.execute_get(
            "orizn-visa",
            operation_key,
            query_params=query_params,
            headers=headers,
            read_timeout=_VISA_TIMEOUT_SECONDS,
        )
        body = json.loads(response.content.decode("utf-8"))
    except httpx.HTTPStatusError as error:
        # 429 表示免费额度用尽；403 表示当前计划不包含该接口；401 表示密钥缺失或无效。
        if error.response.status_code == 429:
            return {
                "available": False,
                "error_code": "orizn_visa_quota_exceeded",
                "message": "签证查询额度已用尽，请稍后重试或升级服务计划。",
            }
        if error.response.status_code == 403:
            return {
                "available": False,
                "error_code": "orizn_visa_plan_required",
                "message": "该签证能力需要付费计划，当前仅开放部分签证工具。",
            }
        if error.response.status_code == 401:
            return {
                "available": False,
                "error_code": "orizn_visa_auth_failed",
                "message": "签证服务的访问密钥缺失或无效，请联系管理员。",
            }
        return {
            "available": False,
            "error_code": "orizn_visa_unavailable",
            "message": "签证服务暂不可用，请稍后重试。",
        }
    except (ToolPolicyDenied, httpx.HTTPError, ValueError, UnicodeDecodeError, RuntimeError):
        return {
            "available": False,
            "error_code": "orizn_visa_unavailable",
            "message": "签证服务暂不可用，请稍后重试。",
        }
    return {"available": True, "result": body}


def _visa_request(tool: str, payload: VisaQueryRequest) -> tuple[str, dict[str, str]]:
    """把工具名与请求字段映射为已登记操作与查询参数，缺字段时返回 422。"""
    if tool not in _VISA_TOOLS:
        raise HTTPException(status_code=404, detail="visa_tool_not_registered")
    if tool == "coverage":
        return "visa_stats", {}
    if tool == "changes":
        return "visa_changes", _visa_params(payload, ("passport", "destination", "lang"))
    passport = _require_visa_field(payload.passport, "passport")
    if tool == "quick_check":
        return "visa_check", {
            "passport": passport,
            "destination": _require_visa_field(payload.destination, "destination"),
        }
    if tool == "requirement":
        params = {"passport": passport, "destination": _require_visa_field(
            payload.destination, "destination"
        )}
        return "visa_detail", _with_lang(params, payload.lang)
    if tool == "transit":
        params = {
            "passport": passport,
            "destination": _require_visa_field(payload.transit_country, "transit_country"),
        }
        return "visa_detail", _with_lang(params, payload.lang)
    if tool == "compare":
        destinations = payload.destinations or ()
        if not destinations:
            raise HTTPException(status_code=422, detail="visa_destinations_required")
        for code in destinations:
            _require_visa_field(code, "destinations")
        params = {"passport": passport, "destination": ",".join(destinations)}
        return "visa_bulk", _with_lang(params, payload.lang)
    raise HTTPException(status_code=404, detail="visa_tool_not_registered")


def _visa_params(payload: VisaQueryRequest, names: tuple[str, ...]) -> dict[str, str]:
    """按登记字段名收集非空查询参数。"""
    collected: dict[str, str] = {}
    for name in names:
        value = getattr(payload, name)
        if value is None:
            continue
        collected[name] = _require_visa_field(value, name)
    return collected


def _with_lang(params: dict[str, str], lang: str | None) -> dict[str, str]:
    """把可选语言码并入查询参数，非法值直接拒绝。"""
    if lang is None:
        return params
    return {**params, "lang": _require_visa_lang(lang)}


def _require_visa_field(value: object, name: str) -> str:
    """校验护照与目的地为三字母国家码。"""
    if not isinstance(value, str) or len(value.strip()) != 3 or not value.strip().isalpha():
        raise HTTPException(status_code=422, detail=f"visa_{name}_invalid")
    return value.strip().upper()


def _require_visa_lang(value: object) -> str:
    """校验语言码为两位小写字母。"""
    if not isinstance(value, str) or len(value.strip()) != 2 or not value.strip().isalpha():
        raise HTTPException(status_code=422, detail="visa_lang_invalid")
    return value.strip().lower()


def _parse_iso_date(value: str) -> date | None:
    """解析 YYYY-MM-DD 日期，格式非法时返回 None。"""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _bailian_configured(settings: GatewaySettings) -> bool:
    """检查百炼知识库定位参数和两项 AccessKey Secret 是否存在。"""
    return bool(
        settings.bailian_workspace_id.strip()
        and settings.bailian_index_id.strip()
        and Path(settings.bailian_access_key_id_file).is_file()
        and Path(settings.bailian_access_key_secret_file).is_file()
    )


def _read_bailian_secret(path: str, minimum_length: int) -> str:
    """读取百炼 AccessKey 文件并拒绝空值，绝不返回到日志或响应。"""
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise HTTPException(status_code=503, detail="bailian_secret_unavailable") from error
    if len(value) < minimum_length:
        raise HTTPException(status_code=503, detail="bailian_secret_invalid")
    return value


def _retrieve_bailian_sync(settings: GatewaySettings, query: str) -> dict[str, object]:
    """通过官方 SDK 调用 Retrieve，并将响应裁剪为安全摘要。"""
    from alibabacloud_bailian20231229 import models as BailianModels
    from alibabacloud_bailian20231229.client import Client as BailianClient
    from alibabacloud_tea_openapi.models import Config as BailianConfig
    from alibabacloud_tea_util.models import RuntimeOptions

    client = BailianClient(
        BailianConfig(
            access_key_id=_read_bailian_secret(settings.bailian_access_key_id_file, 8),
            access_key_secret=_read_bailian_secret(settings.bailian_access_key_secret_file, 16),
            protocol="https",
            endpoint="bailian.cn-beijing.aliyuncs.com",
            read_timeout=10000,
            connect_timeout=3000,
        )
    )
    response = client.retrieve_with_options(
        settings.bailian_workspace_id,
        BailianModels.RetrieveRequest(index_id=settings.bailian_index_id, query=query),
        {},
        RuntimeOptions(),
    )
    body = response.body
    if body is None or body.success is False:
        raise RuntimeError("bailian_retrieve_failed")
    data = body.data
    nodes = [] if data is None or data.nodes is None else data.nodes
    safe_nodes: list[dict[str, object]] = []
    for node in nodes[:8]:
        text = (node.text or "").strip()
        if not text:
            continue
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        source = metadata.get("doc_name") or metadata.get("file_name") or metadata.get("title")
        safe_nodes.append(
            {
                "text": text[:3000],
                "score": node.score,
                "source": str(source)[:200] if source else "百炼知识库",
            }
        )
    return {"nodes": safe_nodes, "count": len(safe_nodes)}


async def _retrieve_bailian(settings: GatewaySettings, query: str) -> dict[str, object]:
    """在线程池执行 SDK 阻塞调用，避免阻塞 Tool Gateway 事件循环。"""
    try:
        return await asyncio.to_thread(_retrieve_bailian_sync, settings, query.strip())
    except HTTPException:
        raise
    except Exception as error:
        # RAM 子账号缺少 sfm:Retrieve 授权时给出可诊断的稳定错误码，而不是笼统的检索失败。
        if _is_permission_denied(error):
            raise HTTPException(status_code=503, detail="bailian_permission_denied") from error
        raise HTTPException(status_code=502, detail="bailian_retrieve_failed") from error


def _is_permission_denied(error: BaseException) -> bool:
    """识别阿里云 IAM 隐式拒绝（NoPermission / AccessDenied / sfm:Retrieve）。"""
    text = str(error)
    return any(
        marker in text
        for marker in ("NoPermission", "AccessDenied", "sfm:Retrieve", "ImplicitDeny")
    )


app = create_app()
