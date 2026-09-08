# 本文件验证 API Server 请求摘要脱敏规则。
# 定义敏感字段、查询字段和非 JSON 正文脱敏测试函数。
from travel_agent_api.core.redaction import summarize_request


def test_request_summary_keeps_only_non_sensitive_field_names() -> None:
    """请求摘要只能保留白名单字段名，敏感字段统一显示为脱敏标记。"""
    summary = summarize_request(
        b'{"account":"travel.user","password":"secret","prompt":"hidden"}',
        {"page": "1", "access_token": "hidden"},
    )

    assert summary["field_names"] == ["account"]
    assert summary["redacted_fields"] == ["<redacted>", "<redacted>"]
    assert summary["query_field_names"] == ["page"]
    assert summary["query_redacted_fields"] == ["<redacted>"]


def test_non_json_request_body_never_enters_summary() -> None:
    """无法解析的正文只能记录长度，不能被当作日志文本保留。"""
    summary = summarize_request(b"password=secret", {})

    assert summary["content_length"] == len(b"password=secret")
    assert summary["field_names"] == []
    assert summary["redacted_fields"] == []
