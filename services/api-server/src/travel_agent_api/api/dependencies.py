# 本文件定义认证、CSRF 和权限相关的 FastAPI 依赖项。
# 定义 get_auth_service、get_current_user、require_csrf 与 require_current_admin。
from __future__ import annotations

from typing import cast

from fastapi import Depends, Request

from travel_agent_api.api.errors import ServiceError
from travel_agent_api.application.access_policy import require_admin
from travel_agent_api.application.auth_service import AuthenticatedUser, AuthService
from travel_agent_api.core.correlation import get_correlation_context


def get_auth_service(request: Request) -> AuthService:
    """从应用状态获取 P0 内存认证服务。"""
    return cast(AuthService, request.app.state.auth_service)


async def get_current_user(
    request: Request, auth_service: AuthService = Depends(get_auth_service)
) -> AuthenticatedUser:
    """验证 HttpOnly 会话 Cookie 并返回当前用户。"""
    settings = request.app.state.settings
    user = await auth_service.get_authenticated_user(
        request.cookies.get(settings.session_cookie_name)
    )
    if user is None:
        trace_id, request_id = audit_context(request)
        await record_audit_event(request, "authentication_denied", None, "denied")
        raise ServiceError("unauthenticated", "请先登录", False, 401)
    return user


def require_csrf(request: Request) -> None:
    """执行 Double Submit CSRF 校验，缺失或不匹配时统一拒绝。"""
    settings = request.app.state.settings
    if not settings.csrf_enabled:
        return
    cookie_value = request.cookies.get(settings.csrf_cookie_name)
    header_value = request.headers.get(settings.csrf_header_name)
    if not cookie_value or not header_value or cookie_value != header_value:
        raise ServiceError("csrf_failed", "请求校验失败", False, 403)


async def require_current_admin(
    request: Request, auth_service: AuthService = Depends(get_auth_service)
) -> AuthenticatedUser:
    """验证当前会话和管理员权限，并记录调用方由路由层审计。"""
    user = await get_current_user(request, auth_service)
    try:
        require_admin(user)
    except ServiceError:
        await record_audit_event(request, "admin_access_denied", user.user_id, "denied")
        raise
    return user


def audit_context(request: Request) -> tuple[str, str]:
    """返回当前请求的关联标识，用于不含敏感内容的业务审计。"""
    context = get_correlation_context(request)
    return context.trace_id, context.request_id


async def record_audit_event(
    request: Request,
    event_type: str,
    actor_user_id: str | None,
    outcome: str,
) -> None:
    """写入含四类关联标识的脱敏业务审计，不记录接口原始正文。"""
    context = get_correlation_context(request)
    await request.app.state.audit_service.record(
        event_type,
        actor_user_id,
        outcome,
        context.trace_id,
        context.request_id,
        context.run_id,
        context.thread_id,
    )
