# 文件职责：校验工具错误转译中间件把已知业务错误转成模型可见提示、未知异常继续冒泡。
# 定义 safe_tool_error_text 映射测试、中间件装配测试与中断信号不被拦截的回归测试。
from __future__ import annotations

import asyncio
from typing import Any, cast

from langchain.agents.middleware import ToolErrorMiddleware

from travel_agent_agent.agents.common.tool_errors import (
    build_tool_error_middleware,
    safe_tool_error_text,
)
from travel_agent_agent.agents.itinerary_manage.client import TravelManageApiError
from travel_agent_agent.infrastructure.dashscope_client import DashScopeGatewayError
from travel_agent_agent.infrastructure.tuniu_client import TuniuProviderError


def test_known_business_errors_map_to_stable_chinese_hints() -> None:
    """内部差旅接口、知识库与外部预订平台的已知错误码都应返回中文可展示提示。"""
    assert safe_tool_error_text(TravelManageApiError("profile_incomplete")) == (
        "您的档案缺少必要信息（如职级），无法给出精确标准，可先参考通用制度。"
    )
    assert safe_tool_error_text(TravelManageApiError("profile_not_found")) == (
        "未找到您的用户档案，无法查询个性化数据。"
    )
    knowledge_hint = safe_tool_error_text(
        DashScopeGatewayError("dashscope_model_unavailable")
    )
    assert knowledge_hint is not None
    assert safe_tool_error_text(TuniuProviderError("tuniu_provider_disabled")) == (
        "预订服务当前未启用。"
    )


def test_unknown_errors_and_unknown_codes_stay_safe() -> None:
    """未知异常类型必须返回 None 以便继续冒泡；未知错误码只给通用兜底文案。"""
    assert safe_tool_error_text(RuntimeError("internal boom")) is None
    assert safe_tool_error_text(ValueError("bad input")) is None
    fallback = safe_tool_error_text(TravelManageApiError("travel_order_unknown_failure"))
    assert fallback is not None
    assert "travel_order_unknown_failure" not in fallback


def test_middleware_converts_known_errors_and_passes_through_the_rest() -> None:
    """中间件只转译已知业务错误，取消等控制流信号与未知异常必须继续冒泡。"""
    middleware = build_tool_error_middleware()
    assert isinstance(middleware, ToolErrorMiddleware)
    handler = cast(Any, middleware).aon_error
    assert handler is not None

    async def run() -> tuple[object, object, object]:
        """在事件循环内调用异步错误处理器，模拟图内工具异常路径。"""
        converted = await handler(TravelManageApiError("profile_not_found"), None)
        unknown = await handler(RuntimeError("boom"), None)
        cancelled = await handler(asyncio.CancelledError(), None)
        return converted, unknown, cancelled

    converted, unknown, cancelled = asyncio.run(run())

    assert converted is not None
    assert unknown is None
    assert cancelled is None
