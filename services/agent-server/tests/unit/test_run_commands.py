# 本文件验证 Agent Server 的 Run 启动、恢复与取消内部命令。
# 定义测试客户端、命令载荷与用例，覆盖幂等启动、恢复、取消、检查点过期、
# 直达子 Agent 的正常完成状态迁移以及上游额度错误的归一化。
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from travel_agent_agent.agents.master.agent import MasterAgent
from travel_agent_agent.api.internal_commands import _normalize_execution_error
from travel_agent_agent.core.settings import Settings
from travel_agent_agent.main import create_app
from travel_agent_agent.orchestration.checkpoints import InMemoryRunCheckpointStore


def _create_client(tmp_path) -> tuple[TestClient, str]:
    """创建注入内存检查点替身与临时内部 Token 的 Agent 测试客户端。"""
    token = "b" * 32
    token_file = tmp_path / "api_agent_internal_token"
    token_file.write_text(token, encoding="utf-8")
    settings = Settings.from_environment(
        {
            "TRAVEL_AGENT_ENV": "development",
            "TRAVEL_AGENT_EXTERNAL_MODE": "real_readonly",
            "API_AGENT_INTERNAL_TOKEN_FILE": str(token_file),
        }
    )
    return TestClient(create_app(settings, InMemoryRunCheckpointStore({}))), token


def _payload(command: str, *, task_brief: str | None = "查询北京到上海的航班") -> dict[str, object]:
    """构造满足运行级内部命令契约的最小请求体。"""
    payload: dict[str, object] = {
        "command_version": "v1",
        "command": command,
        "request_id": "request_001",
        "trace_id": "trace_001",
        "user": {"user_id": "user_001", "role": "user", "privacy_status": "active"},
        "conversation_id": "conversation_001",
        "run_id": "run_001",
        "thread_id": "thread_001",
    }
    if task_brief is not None:
        payload["task_brief"] = task_brief
        payload["context_summary"] = ""
    return payload


def _headers(token: str) -> dict[str, str]:
    """构造匹配命令正文与四类关联标识的内部调用请求头。"""
    return {
        "Authorization": f"Bearer {token}",
        "X-Request-Id": "request_001",
        "X-Trace-Id": "trace_001",
        "X-Run-Id": "run_001",
        "X-Thread-Id": "thread_001",
        "X-Internal-User-Id": "user_001",
        "X-Internal-User-Role": "user",
        "X-Internal-Privacy-Status": "active",
    }


def test_start_run_is_idempotent(tmp_path) -> None:
    """航班查询直达预订智能体且幂等：相同身份、会话和线程重复提交返回原检查点。"""
    client, token = _create_client(tmp_path)

    first = client.post(
        "/internal/v1/commands/runs/start", headers=_headers(token), json=_payload("start")
    )
    second = client.post(
        "/internal/v1/commands/runs/start", headers=_headers(token), json=_payload("start")
    )

    assert first.status_code == 200
    assert first.json()["status"] == "running"
    # 直达路由不改变状态（RUNNING → RUNNING 为无操作），因此版本停在 start 后的 2。
    assert first.json()["version"] == 2
    assert first.json()["routing_action"] == "direct_dispatch"
    assert first.json()["target_agent"] == "bookingAgent"
    assert first.json()["intent_code"] == "flight_search"
    assert first.json()["intent_source"] == "RULE"
    assert second.json() == first.json()


def test_resume_run_rejects_mismatched_conversation(tmp_path) -> None:
    """恢复命令不得借用同一 Run 标识跨会话访问既有检查点。"""
    client, token = _create_client(tmp_path)
    client.post("/internal/v1/commands/runs/start", headers=_headers(token), json=_payload("start"))
    resume = _payload("resume")
    resume["conversation_id"] = "conversation_002"

    response = client.post(
        "/internal/v1/commands/runs/resume", headers=_headers(token), json=resume
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "internal_run_context_mismatch"


def test_cancelled_run_cannot_resume(tmp_path) -> None:
    """取消后的终态 Run 再次恢复时保持 cancelled，不应创建新的执行状态。"""
    client, token = _create_client(tmp_path)
    client.post("/internal/v1/commands/runs/start", headers=_headers(token), json=_payload("start"))

    cancelled = client.post(
        "/internal/v1/commands/runs/cancel",
        headers=_headers(token),
        json=_payload("cancel", task_brief=None),
    )
    resumed = client.post(
        "/internal/v1/commands/runs/resume", headers=_headers(token), json=_payload("resume")
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "cancelled"


def test_resume_run_rejects_missing_checkpoint(tmp_path) -> None:
    """Redis 检查点不存在或已过期时应返回可识别的 409，而不泄漏存储细节。"""
    client, token = _create_client(tmp_path)

    response = client.post(
        "/internal/v1/commands/runs/resume", headers=_headers(token), json=_payload("resume")
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_checkpoint_unavailable"


def test_multi_intent_is_kept_by_master_for_clarification(tmp_path) -> None:
    """L0 识别到强连词多意图时不得直跳子 Agent，而应转入主控澄清状态。"""
    client, token = _create_client(tmp_path)
    payload = _payload("start", task_brief="帮我查询航班，然后预订酒店")

    response = client.post(
        "/internal/v1/commands/runs/start", headers=_headers(token), json=payload
    )

    assert response.status_code == 200
    assert response.json()["status"] == "clarifying"
    assert response.json()["routing_action"] == "clarify"
    assert response.json()["target_agent"] == "masterAgent"
    assert response.json()["intent_code"] == "unknown"


def test_openai_compatible_quota_error_is_normalized() -> None:
    """OpenAI 兼容 SDK 包装后的上游额度错误仍应被归一化为稳定诊断码。"""
    error = RuntimeError("provider request failed")
    error.body = {"error": {"code": "insufficient_quota"}}  # type: ignore[attr-defined]

    code, retryable = _normalize_execution_error(error, "master_agent_execution_failed")

    assert code == "dashscope_quota_exhausted"
    assert retryable is False


def _provider_client(tmp_path) -> tuple[TestClient, Any, str]:
    """构造已审批 Provider 的测试客户端，并注入可替换的 Master 替身。"""
    token = "b" * 32
    token_file = tmp_path / "api_agent_internal_token"
    token_file.write_text(token, encoding="utf-8")
    (tmp_path / "agent_gateway_internal_token").write_text("g" * 48, encoding="utf-8")
    settings = Settings.from_environment(
        {
            "TRAVEL_AGENT_ENV": "development",
            "TRAVEL_AGENT_EXTERNAL_MODE": "real_readonly",
            "API_AGENT_INTERNAL_TOKEN_FILE": str(token_file),
            "AGENT_GATEWAY_INTERNAL_TOKEN_FILE": str(
                tmp_path / "agent_gateway_internal_token"
            ),
            "TRAVEL_AGENT_PROVIDER_KEY": "dashscope",
            "TRAVEL_AGENT_PROVIDER_VERSION": "intent-v1",
            "TRAVEL_AGENT_PROVIDER_BASE_URL": (
                "https://ws-afyh9lpghkjx1iz8.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
            ),
            "TRAVEL_AGENT_PROVIDER_SECRET_REF": "dashscope_api_key",
            "TRAVEL_AGENT_PROVIDER_APPROVED": "true",
            "TUNIU_API_KEY_FILE": str(tmp_path / "tuniu_api_key"),
        }
    )
    application = create_app(settings, InMemoryRunCheckpointStore({}))
    master = MasterAgent.create_with_default_provider(object(), settings=settings)

    async def astream(
        message: str,
        rewritten_question: str,
        intent_result_json: str,
        session_id: str | None = None,
        context: object | None = None,
    ) -> Any:
        """用固定分片与最终状态替代真实流式模型调用，避免访问外部网络。"""
        del message, rewritten_question, intent_result_json, session_id, context
        yield ("delta", "已为您查询到该差旅单的")
        yield (
            "final",
            {"messages": [SimpleNamespace(content="已为您查询到该差旅单的审批状态。")]},
        )

    master.astream = astream  # type: ignore[method-assign]
    application.state.master_agent = master
    return TestClient(application), settings, token


def test_direct_dispatch_without_interrupt_completes_run(tmp_path) -> None:
    """直达子 Agent 且无待交互时，Run 必须落到 completed 并返回助手回复。"""
    client, _, token = _provider_client(tmp_path)

    response = client.post(
        "/internal/v1/commands/runs/start",
        headers=_headers(token),
        json=_payload("start", task_brief="帮我预订酒店"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert response.json()["assistant_reply"] == "已为您查询到该差旅单的审批状态。"
