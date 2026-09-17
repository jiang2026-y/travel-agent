# 文件职责：定义用户级第三方 API Key 的检查与保存工具。
# 定义 ProviderSpec、ApiKeyTools 及 provider 注册表，密钥正文只经内部接口加密落库。
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.tools import StructuredTool

from travel_agent_agent.agents.itinerary_manage.client import TravelManageApiClient


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """保存第三方服务的 provider 名、展示名、获取地址与 Key 前缀。"""

    provider: str
    display_name: str
    guide_url: str
    key_prefix: str


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        provider="tuniu-cli",
        display_name="途牛旅行服务",
        guide_url="https://open.tuniu.com/mcp/login",
        key_prefix="sk-",
    ),
    ProviderSpec(
        provider="flight-manager",
        display_name="机票服务",
        guide_url="https://h5.133.cn/webapp/pages/mcpApiKey",
        key_prefix="sk_",
    ),
)


class ApiKeyTools:
    """提供按 provider 检查与保存用户 API Key 的工具集合。"""

    def __init__(self, client: TravelManageApiClient) -> None:
        """保存内部 API 客户端，不在 Agent 侧保存或缓存明文密钥。"""
        self.client = client

    async def check_tuniu_api_key(self) -> dict[str, Any]:
        """检查当前用户是否已配置途牛 API Key。"""
        return await self.check_api_key("tuniu-cli")

    async def save_tuniu_api_key(self, api_key: str) -> dict[str, Any]:
        """校验前缀后保存用户途牛 API Key。"""
        return await self.save_api_key("tuniu-cli", api_key)

    async def check_flight_api_key(self) -> dict[str, Any]:
        """检查当前用户是否已配置机票服务 API Key。"""
        return await self.check_api_key("flight-manager")

    async def save_flight_api_key(self, api_key: str) -> dict[str, Any]:
        """校验前缀后保存用户机票服务 API Key。"""
        return await self.save_api_key("flight-manager", api_key)

    async def check_api_key(self, provider: str) -> dict[str, Any]:
        """调用内部接口读取配置状态，不返回密钥正文。"""
        spec = _require_spec(provider)
        result = await self.client.request("GET", f"/internal/v1/users/api-keys/{spec.provider}")
        has_key = bool(result.get("has_key"))
        if has_key:
            return {
                "hasKey": True,
                "message": f"{spec.display_name} API Key 已配置，可以直接使用。",
            }
        return {
            "hasKey": False,
            "guide_url": spec.guide_url,
            "message": (
                f"尚未配置{spec.display_name} API Key。请引导用户前往 {spec.guide_url} 获取，"
                f"拿到后由用户提供，再调用保存工具写入。"
            ),
        }

    async def save_api_key(self, provider: str, api_key: str) -> dict[str, Any]:
        """校验非空与前缀后保存密钥，密钥正文不写日志。"""
        spec = _require_spec(provider)
        if not isinstance(api_key, str) or not api_key.strip():
            return {"success": False, "message": "API Key 不能为空。"}
        normalized = api_key.strip()
        if not normalized.startswith(spec.key_prefix):
            return {
                "success": False,
                "message": f"API Key 格式不正确，应以 {spec.key_prefix} 开头。",
            }
        await self.client.request(
            "PUT",
            f"/internal/v1/users/api-keys/{spec.provider}",
            body={"api_key": normalized},
        )
        return {
            "success": True,
            "message": f"{spec.display_name} API Key 已保存，后续调用将自动使用。",
        }

    def as_tools(self) -> list[StructuredTool]:
        """返回全部 API Key 管理工具。"""
        return [
            StructuredTool.from_function(
                coroutine=self.check_tuniu_api_key,
                name="check_tuniu_api_key",
                description=(
                    "检查当前用户是否已配置途牛 API Key；调用途牛服务前必须先检查。"
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.save_tuniu_api_key,
                name="save_tuniu_api_key",
                description="保存用户提供的途牛 API Key（sk- 开头）。",
            ),
            StructuredTool.from_function(
                coroutine=self.check_flight_api_key,
                name="check_flight_api_key",
                description="检查当前用户是否已配置机票服务 API Key；调用机票服务前必须先检查。",
            ),
            StructuredTool.from_function(
                coroutine=self.save_flight_api_key,
                name="save_flight_api_key",
                description="保存用户提供的机票服务 API Key（sk_ 开头）。",
            ),
        ]


def _require_spec(provider: str) -> ProviderSpec:
    """按 provider 名读取注册配置，未注册时拒绝。"""
    for spec in PROVIDERS:
        if spec.provider == provider:
            return spec
    raise ValueError("api_key_provider_not_registered")
