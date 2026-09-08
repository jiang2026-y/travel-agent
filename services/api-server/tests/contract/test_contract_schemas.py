# 本文件验证 API、SSE 与 Agent 命令版本化 Schema 的基础兼容性。
# 定义读取契约、验证停止记忆命令、验证 SSE 事件及验证 OpenAPI 安全边界的测试函数。
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_CONTRACT_ROOT = _REPOSITORY_ROOT / "packages" / "contracts"


def _load_json_schema(relative_path: str) -> dict[str, object]:
    """读取并返回版本化 JSON Schema，不依赖运行时服务实现。"""
    return json.loads((_CONTRACT_ROOT / relative_path).read_text(encoding="utf-8"))


def test_suspend_user_memory_contract_requires_user_level_correlation() -> None:
    """用户级停止记忆命令必须要求用户安全上下文和调用链标识，不要求会话或 Run 标识。"""
    schema = _load_json_schema("commands/agent-command-v1.schema.json")
    valid_command = {
        "command_version": "v1",
        "command": "suspend_user_memory",
        "request_id": "request_001",
        "trace_id": "trace_001",
        "user": {
            "user_id": "user_001",
            "role": "user",
            "privacy_status": "deletion_pending",
        },
        "reason": "account_deletion_requested",
    }

    jsonschema.validate(valid_command, schema)
    assert "conversation_id" not in schema["$defs"]["SuspendUserMemoryCommand"]["required"]
    assert "run_id" not in schema["$defs"]["SuspendUserMemoryCommand"]["required"]
    assert "thread_id" not in schema["$defs"]["SuspendUserMemoryCommand"]["required"]


def test_sse_schema_rejects_sensitive_or_unknown_event_data() -> None:
    """SSE 事件仅允许声明的数据字段，敏感或未知字段不得越过浏览器边界。"""
    schema = _load_json_schema("events/sse-event-v1.schema.json")
    valid_event = {
        "event_id": "event_001",
        "run_id": "run_001",
        "timestamp": "2026-08-18T12:00:00Z",
        "type": "token",
        "data": {"text": "正在整理行程信息"},
        "trace_id": "trace_001",
    }
    invalid_event = {
        **valid_event,
        "data": {"text": "正在整理行程信息", "authorization": "must-not-pass"},
    }

    jsonschema.validate(valid_event, schema)
    try:
        jsonschema.validate(invalid_event, schema)
    except jsonschema.ValidationError:
        pass
    else:
        raise AssertionError("SSE Schema 必须拒绝未知或敏感事件字段")


def test_openapi_declares_preprovisioned_account_login_boundary() -> None:
    """浏览器 API 契约必须声明预置账号密码登录、Cookie 和 CSRF 边界。"""
    document = yaml.safe_load(
        (_CONTRACT_ROOT / "openapi/travel-agent-v1.yaml").read_text(encoding="utf-8")
    )

    assert document["openapi"].startswith("3.1.")
    assert "/api/v1/auth/login" in document["paths"]
    assert "/api/v1/auth/register" not in document["paths"]
    assert "/api/v1/privacy-notice" not in document["paths"]
    assert "/api/v1/account/deletion-requests" not in document["paths"]
    login_request = document["components"]["schemas"]["CredentialLoginRequest"]
    assert login_request["required"] == ["account", "password"]
    assert "cookieAuth" in document["components"]["securitySchemes"]
