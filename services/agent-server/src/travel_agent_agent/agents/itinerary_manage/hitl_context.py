# 本文件保存一次恢复执行期间的确认授权，不写入模型消息、检查点或日志。
# 定义授权数据类及绑定、读取、清除当前工具授权的函数。
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolExecutionAuthorization:
    """表示已通过前端确认、仅可用于当前工具执行的短生命周期授权。"""

    interaction_id: str
    confirmation_token: str


_ACTIVE_AUTHORIZATION: ContextVar[ToolExecutionAuthorization | None] = ContextVar(
    "itinerary_manage_tool_authorization", default=None
)


def bind_tool_execution_authorization(
    authorization: ToolExecutionAuthorization,
) -> Token[ToolExecutionAuthorization | None]:
    """将确认凭证绑定到当前恢复协程，不允许跨请求传播。"""
    return _ACTIVE_AUTHORIZATION.set(authorization)


def reset_tool_execution_authorization(token: Token[ToolExecutionAuthorization | None]) -> None:
    """在工具执行结束后清除确认凭证。"""
    _ACTIVE_AUTHORIZATION.reset(token)


def get_tool_execution_authorization() -> ToolExecutionAuthorization:
    """读取当前工具授权；不存在时拒绝任何写操作。"""
    authorization = _ACTIVE_AUTHORIZATION.get()
    if authorization is None:
        raise PermissionError("hitl_confirmation_required")
    return authorization
