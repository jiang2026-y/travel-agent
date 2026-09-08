# 本文件验证普通用户和管理员的服务端权限边界。
# 定义当前用户查询、管理员资源和角色自助提升拒绝测试。

from fastapi.testclient import TestClient

from travel_agent_api.main import create_app


def test_user_can_read_self_but_cannot_request_role_elevation() -> None:
    """普通用户只能读取自身身份，不能通过请求体改变服务端角色。"""
    client = TestClient(create_app())
    client.post(
        "/api/v1/auth/login",
        json={"account": "travel.user", "password": "user-password"},
    )

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "user"

    elevation = client.post("/api/v1/auth/role", json={"role": "admin"})
    assert elevation.status_code == 403
    assert elevation.json()["error"]["code"] == "forbidden"


def test_admin_can_read_admin_status_but_user_cannot() -> None:
    """管理员可访问管理状态，普通用户统一收到拒绝。"""
    user_client = TestClient(create_app())
    user_client.post(
        "/api/v1/auth/login",
        json={"account": "travel.user", "password": "user-password"},
    )
    assert user_client.get("/api/v1/admin/status").status_code == 403

    admin_client = TestClient(create_app())
    admin_client.post(
        "/api/v1/auth/login",
        json={"account": "travel.admin", "password": "admin-password"},
    )
    response = admin_client.get("/api/v1/admin/status")
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
