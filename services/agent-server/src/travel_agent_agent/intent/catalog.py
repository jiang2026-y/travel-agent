# 本文件定义旅行助手的版本化意图目录与受控目标 Agent 映射。
# 定义 IntentDefinition，用于保存意图代码、目标 Agent 与中文名称；定义 INTENT_CATALOG，
# 用于为 L1、L2、L3 提供唯一且不可由模型任意扩展的路由白名单。
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IntentDefinition:
    """保存一个意图代码的固定路由目标和中文名称。"""

    code: str
    target_agent: str
    display_name: str


INTENT_CATALOG: dict[str, IntentDefinition] = {
    "travel_application": IntentDefinition(
        "travel_application", "itineraryManageAgent", "差旅申请"
    ),
    "travel_cancel": IntentDefinition("travel_cancel", "itineraryManageAgent", "取消差旅"),
    "travel_modify": IntentDefinition("travel_modify", "itineraryManageAgent", "修改差旅"),
    "approval_query": IntentDefinition(
        "approval_query", "itineraryManageAgent", "审批进度查询"
    ),
    "travel_order_query": IntentDefinition(
        "travel_order_query", "itineraryManageAgent", "差旅单查询"
    ),
    "itinerary_planning": IntentDefinition(
        "itinerary_planning", "itineraryPlanAgent", "行程规划"
    ),
    "flight_search": IntentDefinition("flight_search", "itineraryPlanAgent", "航班查询"),
    "train_search": IntentDefinition("train_search", "itineraryPlanAgent", "火车查询"),
    "hotel_search": IntentDefinition("hotel_search", "itineraryPlanAgent", "酒店查询"),
    "booking": IntentDefinition("booking", "bookingAgent", "预订处理"),
    "reimbursement": IntentDefinition("reimbursement", "masterAgent", "报销咨询"),
    "policy_query": IntentDefinition("policy_query", "infoAgent", "差旅政策查询"),
    "attractions_query": IntentDefinition("attractions_query", "infoAgent", "目的地游览信息查询"),
    "general_info": IntentDefinition("general_info", "infoAgent", "通用出行信息查询"),
    "greeting": IntentDefinition("greeting", "masterAgent", "问候"),
    "unknown": IntentDefinition("unknown", "masterAgent", "待确认诉求"),
}


def get_intent_definition(intent_code: str) -> IntentDefinition:
    """按代码读取白名单意图；未知代码一律拒绝，防止动态路由。"""
    try:
        return INTENT_CATALOG[intent_code]
    except KeyError as error:
        raise ValueError(f"intent_code_not_allowlisted:{intent_code}") from error
