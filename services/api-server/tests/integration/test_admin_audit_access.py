# 本文件验证登录、权限拒绝与管理员审计查询的 P0 内存闭环。
# 定义普通用户审计拒绝及管理员读取脱敏审计元数据的测试。

from fastapi.testclient import TestClient

from travel_agent_api.main import create_app


def test_only_admin_can_read_audit_and_query_is_audited() -> None:
    """普通用户不能读取审计，管理员读取后应产生自身的查询审计事实。"""
    application = create_app()
    user_client = TestClient(application)
    user_client.post(
        "/api/v1/auth/login",
        json={"account": "travel.user", "password": "user-password"},
    )
    denied = user_client.get("/api/v1/admin/audit")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    assert application.state.audit_service.entries[-1].event_type == "admin_access_denied"

    admin_client = TestClient(application)
    admin_client.post(
        "/api/v1/auth/login",
        json={"account": "travel.admin", "password": "admin-password"},
    )
    response = admin_client.get("/api/v1/admin/audit")
    assert response.status_code == 200
    assert all("password" not in str(entry) for entry in response.json()["entries"])
    assert application.state.audit_service.entries[-1].event_type == "admin_audit_read"
