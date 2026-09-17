# 文件职责：定义 InfoAgent 的签证查询工具集合。
# 定义 VisaTools，把六个签证能力暴露为中文描述的只读 LangChain 工具。
from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from travel_agent_agent.infrastructure.visa_client import VisaClient

_ISO3_HINT = "国家代码为三位字母 ISO3 码，如 CHN（中国）、THA（泰国）、JPN（日本）"


class VisaTools:
    """封装签证要求、过境签、目的地对比与政策变更查询。"""

    def __init__(self, client: VisaClient) -> None:
        """保存经网关出网的签证客户端。"""
        self.client = client

    async def quick_visa_check(self, passport: str, destination: str) -> dict[str, Any]:
        """快速判断是否需要签证。"""
        return await self.client.quick_check(passport, destination)

    async def check_visa_requirement(
        self, passport: str, destination: str, lang: str | None = None
    ) -> dict[str, Any]:
        """查询完整入境要求。"""
        return await self.client.requirement(passport, destination, lang)

    async def check_transit_visa(
        self, passport: str, transit_country: str, lang: str | None = None
    ) -> dict[str, Any]:
        """查询过境签要求。"""
        return await self.client.transit(passport, transit_country, lang)

    async def compare_destinations(
        self, passport: str, destinations: list[str], lang: str | None = None
    ) -> dict[str, Any]:
        """对比多个目的地的签证要求。"""
        return await self.client.compare(passport, destinations, lang)

    async def get_recent_changes(
        self,
        passport: str | None = None,
        destination: str | None = None,
        lang: str | None = None,
    ) -> dict[str, Any]:
        """查询近期签证政策变更。"""
        return await self.client.recent_changes(passport, destination, lang)

    async def get_coverage_stats(self) -> dict[str, Any]:
        """查询签证数据覆盖范围。"""
        return await self.client.coverage()

    def as_tools(self) -> list[StructuredTool]:
        """返回全部签证查询工具。"""
        return [
            StructuredTool.from_function(
                coroutine=self.quick_visa_check,
                name="quick_visa_check",
                description=f"快速判断某护照前往某目的地是否需要签证；{_ISO3_HINT}。",
            ),
            StructuredTool.from_function(
                coroutine=self.check_visa_requirement,
                name="check_visa_requirement",
                description=(
                    f"查询完整入境要求（材料、费用、办理时长等）；{_ISO3_HINT}；"
                    "可选 lang 为两位语言码，如 zh、en。"
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.check_transit_visa,
                name="check_transit_visa",
                description=(
                    f"查询经第三国中转时的过境签要求；{_ISO3_HINT}；"
                    "transit_country 为中转国 ISO3 码。"
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.compare_destinations,
                name="compare_destinations",
                description=(
                    f"对比同一次出行中多个目的地的签证要求；{_ISO3_HINT}；"
                    "destinations 为 ISO3 码列表，最多 8 个。"
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.get_recent_changes,
                name="get_recent_changes",
                description="查询近期签证政策变更，可按护照国或目的地过滤。",
            ),
            StructuredTool.from_function(
                coroutine=self.get_coverage_stats,
                name="get_coverage_stats",
                description="查询签证数据覆盖范围（支持的国家对数量与语言）。",
            ),
        ]
