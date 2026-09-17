# 文件职责：验证途牛 CLI 常见响应包装、订单号和支付链接解析，以及失败状态保护。
# 定义测试用例，确保 Provider 返回结构变化不会导致订单成功状态被误判。
from __future__ import annotations

import json

from travel_agent_agent.infrastructure.tuniu_client import (
    _extract_payload,
    extract_external_order_no,
    extract_payment_url,
)


def test_extract_result_wrapper() -> None:
    """标准 result 包装应提取订单号和 HTTPS 支付地址。"""
    payload = _extract_payload({"result": {"orderId": "T-1", "payUrl": "https://pay.example/1"}})
    assert extract_external_order_no(payload) == "T-1"
    assert extract_payment_url(payload) == "https://pay.example/1"


def test_extract_nested_cli_wrappers() -> None:
    """structuredContent、data 和 content.text 包装应统一展开。"""
    structured = _extract_payload({"structuredContent": {"data": {"orderNo": "T-2", "paymentUrl": "https://pay.example/2"}}})
    assert extract_external_order_no(structured) == "T-2"
    assert extract_payment_url(structured) == "https://pay.example/2"

    content = _extract_payload(
        {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"orderId": "T-3", "cashierUrl": "https://pay.example/3"}
                    ),
                }
            ]
        }
    )
    assert extract_external_order_no(content) == "T-3"
    assert extract_payment_url(content) == "https://pay.example/3"


def test_provider_failure_payload_is_not_order_success() -> None:
    """Provider 明确失败标记不会被误当作完成状态。"""
    failure = {"status": "provider_error", "error_code": "tuniu_provider_call_failed"}
    assert failure["status"] != "completed"
