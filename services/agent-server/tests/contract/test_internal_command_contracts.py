# 本文件验证 API→Agent 内部命令的版本化契约。
# 定义合法运行命令通过、缺失关联标识拒绝和非 P0 预留命令边界测试函数。
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[4]
    / "packages/contracts/commands/agent-command-v1.schema.json"
)


def _schema() -> dict[str, object]:
    """加载内部命令 JSON Schema。"""
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _start_command() -> dict[str, object]:
    """返回包含四类关联标识的合法 start 命令。"""
    return {
        "command_version": "v1",
        "command": "start",
        "request_id": "request_001",
        "trace_id": "trace_001",
        "user": {"user_id": "user_001", "role": "user", "privacy_status": "active"},
        "conversation_id": "conversation_001",
        "run_id": "run_001",
        "thread_id": "thread_001",
        "task_brief": "查询行程信息",
        "context_summary": "",
    }


def test_start_command_requires_all_correlation_identifiers() -> None:
    """运行级命令必须包含 request、trace、run 和 thread 标识。"""
    schema = _schema()
    jsonschema.validate(_start_command(), schema)
    for field in ("request_id", "trace_id", "run_id", "thread_id"):
        invalid = _start_command()
        invalid.pop(field, None)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)


def test_suspend_command_remains_non_p0_contract_reservation() -> None:
    """停止记忆命令保留用户级契约，但不要求运行级标识。"""
    schema = _schema()
    assert "非 P0" in str(schema["description"])
    assert "conversation_id" not in schema["$defs"]["SuspendUserMemoryCommand"]["required"]
