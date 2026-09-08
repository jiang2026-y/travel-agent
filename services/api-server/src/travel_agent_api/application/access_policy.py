# 本文件实现 P0 的用户归属和管理员访问策略。
# 定义 require_owner 与 require_admin，用于拒绝越权访问且不泄露资源信息。
from __future__ import annotations

from travel_agent_api.api.errors import ServiceError
from travel_agent_api.application.auth_service import AuthenticatedUser


def require_owner(actor: AuthenticatedUser, owner_user_id: str) -> None:
    """要求操作者属于资源所有者或拥有管理员角色。"""
    if actor.user_id != owner_user_id and actor.role != "admin":
        raise ServiceError("forbidden", "无权访问该资源", False, 403)


def require_admin(actor: AuthenticatedUser) -> None:
    """要求操作者为管理员，不说明目标资源是否存在。"""
    if actor.role != "admin":
        raise ServiceError("forbidden", "无权访问该资源", False, 403)
