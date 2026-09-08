# 本文件提供 P0 登录、退出、当前用户和角色提升拒绝路由。
# 定义 login、logout、get_current_session 与 reject_role_elevation，用于安全会话边界。
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from travel_agent_api.api.dependencies import (
    get_auth_service,
    get_current_user,
    record_audit_event,
    require_csrf,
)
from travel_agent_api.api.errors import ServiceError
from travel_agent_api.application.auth_service import AuthenticatedUser, AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class CredentialLoginRequest(BaseModel):
    """定义不允许额外字段的账号密码登录请求。"""

    model_config = ConfigDict(extra="forbid")

    account: str = Field(pattern=r"^[A-Za-z0-9_.@-]{3,64}$")
    password: str = Field(min_length=1, max_length=256)


class RoleElevationRequest(BaseModel):
    """定义用于验证服务端拒绝角色自助提升的受控请求。"""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=32)


@router.post("/login")
async def login(
    payload: CredentialLoginRequest,
    request: Request,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, object]:
    """验证预置账号后签发 HttpOnly 会话 Cookie 和 CSRF Cookie。"""
    client_ip = request.client.host if request.client else "unknown"
    result = await auth_service.login(payload.account, payload.password, client_ip)
    if result is None:
        await record_audit_event(request, "login_failed", None, "denied")
        raise ServiceError("invalid_credentials", "账号或密码错误", False, 401)
    user, session_id, csrf_token = result
    settings = request.app.state.settings
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        max_age=settings.session_ttl_seconds,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )
    await record_audit_event(request, "login_succeeded", user.user_id, "success")
    return {"user": user.to_public_dict()}


@router.post("/logout", status_code=204, dependencies=[Depends(require_csrf)])
async def logout(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> Response:
    """校验 CSRF 后使当前会话失效并清除浏览器 Cookie。"""
    settings = request.app.state.settings
    auth_service.logout(request.cookies.get(settings.session_cookie_name))
    await record_audit_event(request, "logout", user.user_id, "success")
    response = Response(status_code=204)
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return response


@router.get("/me")
def get_current_session(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    """返回当前会话的非敏感用户身份。"""
    return {"user": user.to_public_dict()}


@router.post("/role")
async def reject_role_elevation(
    payload: RoleElevationRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> None:
    """明确拒绝 P0 的任何自助角色修改请求。"""
    del payload
    await record_audit_event(request, "role_elevation_denied", user.user_id, "denied")
    raise ServiceError("forbidden", "无权访问该资源", False, 403)
