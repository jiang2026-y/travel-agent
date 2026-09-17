# 文件职责：验证待交互的文字回复走结构化恢复通道时不再重复执行意图识别。
# 定义澄清类交互的 resume-decision 路径测试。
from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.agents.master.agent import MasterAgent
from travel_agent_agent.api.internal_commands import _normalize_execution_error  # noqa: F401
from travel_agent_agent.core.settings import Settings
from travel_agent_agent.main import create_app
from travel_agent_agent.orchestration.checkpoints import InMemoryRunCheckpointStore
from travel_agent_agent.orchestration.interactions import (
    InMemoryPendingInteractionStore,
    PendingInteraction,
)


class _CountingIntentAgent:
    """记录调用次数的意图识别替身，用于确认结构化恢复不会触发识别。"""

    def __init__(self) -> None:
        """初始化计数。"""
        self.calls = 0

    async def execute_with_rewrite(self, text: str, context: AgentContext) -> Any:
        """累计调用次数后返回固定识别结果。"""
        del context
        self.calls += 1
        from travel_agent_agent.intent.result import IntentRecognitionResult

        return IntentRecognitionResult.single_rule_hit("booking", "trace", "rule_1"), text

    async def recognize(self, text: str, trace_id: str, history: str = "") -> Any:
        """兼容旧入口，同样计入次数。"""
        del history
        self.calls += 1
        from travel_agent_agent.intent.result import IntentRecognitionResult

        return IntentRecognitionResult.single_rule_hit("booking", trace_id, "rule_1")


def _client(tmp_path) -> tuple[TestClient, Any, _CountingIntentAgent]:
    """构造带替身意图识别与替身主控的应用客户端。"""
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
    interactions = InMemoryPendingInteractionStore({})
    application = create_app(
        settings, InMemoryRunCheckpointStore({}), interaction_store=interactions
    )
    master = MasterAgent.create_with_default_provider(object(), settings=settings)
    resumed: list[dict[str, object]] = []

    async def astream(*_: Any, **__: Any) -> Any:
        """首轮停在澄清交互上，模拟子智能体索要缺失信息。"""
        yield (
            "final",
            {
                "messages": [{"content": "请问您的出发城市是？"}],
                "__interrupt__": [
                    {
                        "kind": "clarification",
                        "question": "请问您的出发城市是？",
                        "ui_type": "text",
                        "allowed_decisions": ["respond"],
                    }
                ],
            },
        )

    async def aresume(resume_value: dict[str, Any], thread_id: str, context: Any) -> Any:
        """记录恢复值，返回继续执行的回复。"""
        del thread_id, context
        resumed.append(resume_value)
        return {"messages": [{"content": "已按您补充的信息继续提交差旅申请。"}]}

    master.astream = astream  # type: ignore[method-assign]
    master.aresume = aresume  # type: ignore[method-assign]
    application.state.master_agent = master
    counter = _CountingIntentAgent()
    application.state.intent_agent = counter
    return TestClient(application), interactions, counter


def _headers(token: str) -> dict[str, str]:
    """构造内部调用请求头。"""
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


def _start_payload() -> dict[str, object]:
    """构造启动命令载荷。"""
    return {
        "command_version": "v1",
        "command": "start",
        "request_id": "request_001",
        "trace_id": "trace_001",
        "user": {"user_id": "user_001", "role": "user", "privacy_status": "active"},
        "conversation_id": "conversation_001",
        "run_id": "run_001",
        "thread_id": "thread_001",
        "task_brief": "帮我申请出差",
        "context_summary": "",
    }


def test_clarification_answer_skips_intent_recognition(tmp_path) -> None:
    """待交互存在时，文字回复应按结构化决定恢复，不再执行意图识别与路由。"""
    client, interactions, counter = _client(tmp_path)

    started = client.post(
        "/internal/v1/commands/runs/start",
        headers=_headers("b" * 32),
        json=_start_payload(),
    )
    assert started.status_code == 200
    calls_after_start = counter.calls

    issued: PendingInteraction
    issued, _token = _issue_clarification(interactions)
    resume = client.post(
        "/internal/v1/commands/runs/resume",
        headers=_headers("b" * 32),
        json={
            **_start_payload(),
            "command": "resume",
            "task_brief": "北京出发，参加行业会议，行程3天",
            "resume_decision": {
                "interaction_id": issued.interaction_id,
                "decision": "respond",
                "message": "北京出发，参加行业会议，行程3天",
            },
        },
    )

    assert resume.status_code == 200, resume.text
    # 关键断言：结构化恢复没有再次调用意图识别。
    assert counter.calls == calls_after_start
    assert resume.json()["assistant_reply"] == "已按您补充的信息继续提交差旅申请。"
    # 回答完成后交互应被标记为已消费，避免前端继续显示旧问题卡片。
    assert interactions.records[issued.interaction_id].status == "consumed"


def _issue_clarification(interactions: Any) -> tuple[PendingInteraction, str]:
    """在交互存储中签发一条澄清类交互。"""
    interaction = PendingInteraction(
        interaction_id="hitl_001",
        kind="clarification",
        status="issued",
        user_id="user_001",
        conversation_id="conversation_001",
        run_id="run_001",
        thread_id="thread_001",
        graph_thread_id="thread_001",
        tool_name=None,
        args_hash=None,
        allowed_decisions=("respond",),
        summary={"question": "请问您的出发城市是？", "ui_type": "text"},
        token_hash=None,
        action_version=1,
        created_at="2026-01-01T00:00:00+00:00",
    )
    return asyncio.run(interactions.issue(interaction))


def _approval_client(tmp_path: Any) -> tuple[TestClient, Any]:
    """构造以审批中断收尾的替身主控，用于回归“提交批准后卡片反复弹出”。"""
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
    interactions = InMemoryPendingInteractionStore({})
    application = create_app(
        settings, InMemoryRunCheckpointStore({}), interaction_store=interactions
    )
    master = MasterAgent.create_with_default_provider(object(), settings=settings)

    async def astream(*_: Any, **__: Any) -> Any:
        """首轮停在审批中断上，模拟写工具等待用户确认。"""
        yield (
            "final",
            {
                "messages": [{"content": "确认信息无误后即可提交差旅申请。"}],
                "__interrupt__": [
                    {
                        "kind": "approval",
                        "action": {
                            "tool_name": "submit_travel_approval",
                            "tool_args": {"departure_city": "北京", "destination": "杭州"},
                        },
                    }
                ],
            },
        )

    async def aresume(*_: Any, **__: Any) -> Any:
        """恢复执行后返回写工具的真实结果文案。"""
        return {"messages": [{"content": "差旅申请已提交成功。"}]}

    master.astream = astream  # type: ignore[method-assign]
    master.aresume = aresume  # type: ignore[method-assign]
    application.state.master_agent = master
    application.state.intent_agent = _CountingIntentAgent()
    return TestClient(application), interactions


def test_approval_decision_stops_card_reappearing(tmp_path: Any) -> None:
    """提交批准决定后，交互查询不得再返回同一张确认卡片。"""
    client, interactions = _approval_client(tmp_path)
    headers = _headers("b" * 32)

    started = client.post(
        "/internal/v1/commands/runs/start", headers=headers, json=_start_payload()
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "awaiting_approval"

    def load_card() -> dict[str, Any] | None:
        """模拟前端轮询读取当前待确认交互。"""
        response = client.get(
            "/internal/v1/commands/runs/run_001/interaction",
            headers=headers,
            params={"conversation_id": "conversation_001", "thread_id": "thread_001"},
        )
        assert response.status_code == 200, response.text
        pending = response.json()["pending_interaction"]
        return pending if isinstance(pending, dict) else None

    card = load_card()
    assert card is not None
    assert card["kind"] == "approval"
    assert card["confirmation_token"]

    resumed = client.post(
        "/internal/v1/commands/runs/resume",
        headers={**headers, "X-Confirmation-Token": str(card["confirmation_token"])},
        json={
            **_start_payload(),
            "command": "resume",
            "resume_decision": {
                "interaction_id": card["interaction_id"],
                "decision": "approve",
            },
        },
    )
    assert resumed.status_code == 200, resumed.text

    # 决定已提交：恢复执行期间轮询不应再拿到同一张卡片。
    assert load_card() is None
    # 记录与 Token 仍保留，写事务仍需原子消费它。
    assert interactions.records[str(card["interaction_id"])].status == "issued"
