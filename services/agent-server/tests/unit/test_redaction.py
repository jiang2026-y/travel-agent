# 本文件验证 Agent Server 请求摘要脱敏规则。
# 定义提示词、思维链、凭据和普通字段的脱敏测试函数。
from travel_agent_agent.core.redaction import summarize_request


def test_agent_summary_redacts_prompt_reasoning_and_credentials() -> None:
    """Agent 日志摘要不得包含提示词、思维链、Token 或其字段名。"""
    summary = summarize_request(
        b'{"task":"plan","prompt":"hidden","chain_of_thought":"hidden","api_key":"hidden"}',
        {},
    )

    assert summary["field_names"] == ["task"]
    assert summary["redacted_fields"] == ["<redacted>", "<redacted>", "<redacted>"]
