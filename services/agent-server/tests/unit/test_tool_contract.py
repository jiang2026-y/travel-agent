# 文件职责：校验各 Agent 注册的工具名与提示词、网关白名单三方一致。
# 定义工具集覆盖、废弃工具名清除与提示词引用可解析测试。
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.agents.booking.api_key_tools import ApiKeyTools
from travel_agent_agent.agents.booking.skill_tools import SkillTools
from travel_agent_agent.agents.common.travel_order_read_tools import TravelOrderReadTools
from travel_agent_agent.agents.info.tools import DestinationLiveTools, KnowledgeRetrieveTools
from travel_agent_agent.agents.info.visa_tools import VisaTools
from travel_agent_agent.agents.itinerary_manage.tools import (
    BookingReadTools,
    BookingWriteTools,
    PolicyTools,
    QueryUserInfoTools,
    TravelOrderConflictTools,
    TravelOrderWriteTools,
)
from travel_agent_agent.agents.master.memory_tools import PreferenceMemoryTools
from travel_agent_agent.core.settings import Settings
from travel_agent_agent.infrastructure.memory_client import BailianMemoryClient
from travel_agent_agent.infrastructure.visa_client import VisaClient
from travel_agent_agent.intent.catalog import INTENT_CATALOG
from travel_agent_tool_gateway.main import _AGENT_TOOL_NAMES

_PROMPTS_ROOT = (
    Path(__file__).resolve().parents[2] / "src" / "travel_agent_agent" / "prompts"
)
_PROMPT_AGENT_MAP = {
    "masteragent-system.md": "masterAgent",
    "booking-agent-system.md": "bookingAgent",
    "itinerary-manage-agent-system.md": "itineraryManageAgent",
    "info-agent-system.md": "infoAgent",
}
# 提示词中引用但本轮明确不实现的能力，必须逐项可见且不随代码变化。
_PROMPT_ONLY_NAMES = {
    "reimbursement_agent",
    "itinerary_plan_agent",
    "plan_itinerary",
    "review_planner_result",
}
# 其它 Agent 的工具名可能仅作为"禁止向用户暴露内部名称"的反例出现在提示词中。
_PROMPT_EXAMPLE_NAMES = {
    "query_destination_news",
    "query_travel_policy",
    "retrieve_from_memory",
}
_REMOVED_TOOL_NAMES = {"call_sub_agent", "info_query", "cancel_tuniu_booking"}


class _FakeClient:
    """记录请求的最小内部 API 替身，不访问网络。"""

    def __init__(self) -> None:
        self.context = AgentContext(
            trace_id="trace", conversation_id="conv_1", user_id="user_1"
        )

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """返回空对象响应，仅供工具契约构造使用。"""
        del method, path, kwargs
        return {}


class _FakeKnowledge:
    """知识检索替身，返回空结果。"""

    async def retrieve(self, query: str) -> dict[str, object]:
        """返回空检索结果。"""
        del query
        return {"nodes": []}


class _FakeDestination:
    """目的地查询替身，返回未启用结果。"""

    async def query_weather(self, city: str, date: str | None = None) -> dict[str, Any]:
        """返回未启用天气结果。"""
        del city, date
        return {"available": False}

    async def query_destination_news(
        self, city: str, topic: str | None = None
    ) -> dict[str, Any]:
        """返回未启用资讯结果。"""
        del city, topic
        return {"available": False}


class _FakeMemoryClient:
    """长期记忆替身，仅用于工具契约构造。"""

    async def record(self, user_id: str, content: str) -> dict[str, Any]:
        """返回未启用结果。"""
        del user_id, content
        return {"available": False}

    async def retrieve(self, user_id: str, query: str) -> dict[str, Any]:
        """返回空召回。"""
        del user_id, query
        return {"available": False, "memories": ""}


class _FakeVisaClient:
    """签证客户端替身，仅用于工具契约构造。"""

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """返回未启用结果。"""
        del path, payload
        return {"available": False}


def _names(tools: list[Any]) -> set[str]:
    """读取 LangChain 工具名集合。"""
    return {tool.name for tool in tools}


def _settings(tmp_path) -> Settings:
    """构造最小 Settings，仅用于工具装配。"""
    return Settings.from_environment(
        {
            "TOOL_GATEWAY_BASE_URL": "http://tool-gateway:8002",
            "API_SERVER_BASE_URL": "http://api-server:8000",
            "TRAVEL_AGENT_REDIS_URL": "redis://redis:6379/0",
            "TRAVEL_AGENT_EXTERNAL_MODE": "real_tuniu",
            "TRAVEL_AGENT_TUNIU_APPROVED": "true",
            "TUNIU_API_KEY_FILE": str(tmp_path / "tuniu_api_key"),
        }
    )


def test_agent_tool_names_are_covered_by_gateway_whitelist(tmp_path) -> None:
    """每个 Agent 实际注册的工具都必须落在网关对应白名单内。"""
    client = _FakeClient()
    settings = _settings(tmp_path)
    context = client.context
    read_tools = _names(TravelOrderReadTools(client).as_tools())
    profile = _names(QueryUserInfoTools(client).as_tools())
    write_tools = _names(TravelOrderWriteTools(client).as_tools())
    conflict = _names(TravelOrderConflictTools(client).as_tools())
    booking_write = _names(BookingWriteTools(client).as_tools())
    booking_read = _names(BookingReadTools(client).as_tools())
    policy = _names(PolicyTools(client).as_tools())
    api_keys = _names(ApiKeyTools(client).as_tools())
    skills = _names(SkillTools(settings, client).as_tools())
    memory = _names(
        PreferenceMemoryTools(
            cast(BailianMemoryClient, _FakeMemoryClient()), lambda: context
        ).as_tools()
    )
    visa = _names(VisaTools(cast(VisaClient, _FakeVisaClient())).as_tools())
    info = (
        _names(KnowledgeRetrieveTools(_FakeKnowledge(), context).as_tools())
        | _names(DestinationLiveTools(_FakeDestination()).as_tools())
        | policy
        | visa
    )

    assert {
        "submit_travel_approval",
        "cancel_travel_order",
        "modify_travel_order",
        "check_travel_order_conflicts",
        *booking_read,
        "cancel_booking",
        *policy,
        *read_tools,
        *profile,
    } <= _AGENT_TOOL_NAMES["itineraryManageAgent"]
    assert {
        "query_booking_record",
        "cancel_booking",
        *read_tools,
        *profile,
        *api_keys,
        *skills,
        *memory,
    } <= _AGENT_TOOL_NAMES["bookingAgent"]
    assert info <= _AGENT_TOOL_NAMES["infoAgent"]
    assert visa == {
        "quick_visa_check",
        "check_visa_requirement",
        "check_transit_visa",
        "compare_destinations",
        "get_recent_changes",
        "get_coverage_stats",
    }
    assert {
        "ask_user",
        "itinerary_manage_agent",
        "booking_agent",
        "info_agent",
        *memory,
    } <= _AGENT_TOOL_NAMES["masterAgent"]
    assert write_tools <= _AGENT_TOOL_NAMES["itineraryManageAgent"]
    assert conflict <= _AGENT_TOOL_NAMES["itineraryManageAgent"]
    assert booking_write <= _AGENT_TOOL_NAMES["itineraryManageAgent"]


def test_removed_generic_tools_are_absent_from_all_whitelists() -> None:
    """泛化调度工具与旧取消工具名不得残留在任何白名单中。"""
    union = set().union(*_AGENT_TOOL_NAMES.values())
    assert _REMOVED_TOOL_NAMES.isdisjoint(union)


def test_prompt_tool_references_are_resolvable() -> None:
    """提示词引用的已登记工具名必须属于该提示词对应 Agent。"""
    excluded = _PROMPT_ONLY_NAMES | _PROMPT_EXAMPLE_NAMES
    known = set().union(*_AGENT_TOOL_NAMES.values()) | excluded
    pattern = re.compile(r"`([a-z][a-z0-9_]{2,})`")
    for file_name, agent_name in _PROMPT_AGENT_MAP.items():
        content = (_PROMPTS_ROOT / file_name).read_text(encoding="utf-8")
        referenced = {match for match in pattern.findall(content) if match in known}
        unresolved = referenced - _AGENT_TOOL_NAMES[agent_name] - excluded
        assert not unresolved, (file_name, sorted(unresolved))


def test_prompt_include_targets_exist_within_prompts_root() -> None:
    """提示词内所有 {{include:...}} 引用的片段文件都必须能被加载器真实解析。"""
    pattern = re.compile(r"\{\{include:([^}]+)\}\}")
    root = _PROMPTS_ROOT.resolve()
    missing: list[tuple[str, str]] = []
    for prompt_file in sorted(_PROMPTS_ROOT.rglob("*.md")):
        content = prompt_file.read_text(encoding="utf-8")
        for target in pattern.findall(content):
            candidate = (prompt_file.parent / target.strip()).resolve()
            inside_root = root in candidate.parents
            if not inside_root or not candidate.is_file():
                missing.append((prompt_file.name, target.strip()))
    assert not missing, missing


def test_l3_prompt_mapping_matches_intent_catalog() -> None:
    """L3 提示词映射表必须与意图目录逐行一致，否则模型输出会被校验拒绝并退化为 unknown。"""
    content = (_PROMPTS_ROOT / "intent" / "l3_intent_recognition.md").read_text(
        encoding="utf-8"
    )
    row_pattern = re.compile(r"^\|\s*`([a-z_]+)`\s*\|.*?\|\s*`([A-Za-z]+)`\s*\|", re.MULTILINE)
    rows = {intent: agent for intent, agent in row_pattern.findall(content)}

    assert rows == {code: item.target_agent for code, item in INTENT_CATALOG.items()}
    assert "reimbursementAgent" not in content
    assert rows["flight_search"] == "bookingAgent"
    assert rows["train_search"] == "bookingAgent"
    assert rows["hotel_search"] == "bookingAgent"
    assert rows["itinerary_planning"] == "itineraryPlanAgent"


def test_booking_prompt_covers_query_duty_without_conflict() -> None:
    """预订智能体提示词必须包含查询职责，且不再声明"不负责搜索候选"。"""
    content = (_PROMPTS_ROOT / "booking-agent-system.md").read_text(encoding="utf-8")

    assert "不负责搜索候选" not in content
    assert "## 查询流程" in content
    for tool_name in ("search_tuniu_flight", "search_tuniu_train", "search_tuniu_hotel"):
        assert tool_name in content
    assert "查询不需要差旅单" in content
