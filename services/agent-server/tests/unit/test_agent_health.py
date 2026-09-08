# 本文件验证 Agent Server 的真实只读运行基线健康检查。
# 定义 test_agent_health_reports_policy_denied_external_capabilities。
# 该测试确认服务可用且未配置外部能力明确处于策略拒绝状态。

from fastapi.testclient import TestClient

from travel_agent_agent.main import app


def test_agent_health_reports_policy_denied_external_capabilities() -> None:
    """Agent 健康检查必须区分服务正常与未配置外部能力的策略拒绝状态。"""
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "agent-server",
        "status": "ok",
        "external_capabilities": "not_configured_policy_denied",
    }
