# 本文件验证受限 Tool Gateway 的健康检查接口。
# 定义 test_health_reports_dashscope_readonly，用于确保仅登记百炼只读推理能力。
from fastapi.testclient import TestClient

from travel_agent_tool_gateway.main import app


def test_health_reports_dashscope_readonly() -> None:
    """健康接口必须明确仅开放经审查的百炼只读推理能力。"""
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "tool-gateway",
        "status": "ok",
        "external_capabilities": "dashscope_readonly_only",
        "provider_count": 1,
    }
