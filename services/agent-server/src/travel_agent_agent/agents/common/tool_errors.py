# 文件职责：为各 Agent 提供统一的工具错误转译中间件。
# 定义 build_tool_error_middleware 与 safe_tool_error_text，把已知业务错误转为模型可见结果，
# 未知异常继续冒泡；LangGraph 的中断信号永远不会被拦截。
from __future__ import annotations

from typing import Any

from langchain.agents.middleware import ToolErrorMiddleware

from travel_agent_agent.agents.itinerary_manage.client import TravelManageApiError
from travel_agent_agent.infrastructure.dashscope_client import DashScopeGatewayError
from travel_agent_agent.infrastructure.tuniu_client import TuniuProviderError

_FALLBACK_MESSAGES = {
    "travel_api_unavailable": "内部旅行服务暂不可用，请稍后重试；如果反复出现请联系管理员。",
    "profile_not_found": "未找到您的用户档案，无法查询个性化数据。",
    "profile_incomplete": "您的档案缺少必要信息（如职级），无法给出精确标准，可先参考通用制度。",
    "policy_not_found": "未找到适用于您的差旅政策规则，请联系管理员补充档案。",
    "tuniu_provider_disabled": "预订服务当前未启用。",
    "tuniu_provider_not_configured": "预订服务尚未配置，请先补充服务凭据。",
}


def safe_tool_error_text(error: BaseException) -> str | None:
    """把已知业务错误转成稳定的中文提示；未知异常返回 None 以继续冒泡。"""
    if isinstance(error, TravelManageApiError):
        return _FALLBACK_MESSAGES.get(
            getattr(error, "code", "") or str(error),
            "内部差旅接口暂时无法完成该操作，请稍后重试或换一种方式提问。",
        )
    if isinstance(error, DashScopeGatewayError):
        return _FALLBACK_MESSAGES.get(
            getattr(error, "code", "") or "", "模型或知识库服务暂时不可用，请稍后重试。"
        )
    if isinstance(error, TuniuProviderError):
        return _FALLBACK_MESSAGES.get(
            getattr(error, "code", "") or "", "外部预订平台调用失败，请稍后重试或改用其他方案。"
        )
    return None


def build_tool_error_middleware() -> ToolErrorMiddleware:
    """构建把已知业务错误回传给模型的中间件，未知异常与中断信号照常抛出。"""

    async def on_error(error: BaseException, request: Any) -> str | None:
        """按错误类型返回模型可见的中文提示。"""
        del request
        return safe_tool_error_text(error)

    return ToolErrorMiddleware(aon_error=on_error)
