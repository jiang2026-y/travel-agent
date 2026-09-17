# 文件职责：验证 Tool Gateway 接受问题推荐使用的 qwen3.6-flash 裸模型。
# 定义推荐模型请求白名单测试，确保推荐模型不能携带工具调用。
from fastapi.testclient import TestClient

from travel_agent_tool_gateway.main import app


def test_recommendation_model_is_allowlisted_without_tools() -> None:
    """qwen3.6-flash 应被允许，但必须关闭思考且不携带工具。"""
    response = TestClient(app).post(
        "/internal/v1/dashscope/chat-completions",
        json={
            "model": "qwen3.6-flash",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
            "temperature": 0,
            "enable_thinking": False,
        },
    )
    assert response.status_code in {401, 503}
