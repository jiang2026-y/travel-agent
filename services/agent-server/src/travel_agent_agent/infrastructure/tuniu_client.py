# 文件职责：通过服务端 tuniu CLI 受控调用途牛服务。
# 定义 TuniuProviderError、TuniuCliClient 及结果解析辅助函数，负责超时、密钥注入和脱敏。
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any


class TuniuProviderError(RuntimeError):
    """表示途牛 CLI 不可用、调用失败或返回结果无法解析。"""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class TuniuCliClient:
    """使用无 shell 子进程调用 tuniu CLI，避免浏览器或日志接触 API Key。"""

    def __init__(
        self,
        command: str = "tuniu",
        api_key_file: str = "/run/secrets/tuniu_api_key",
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.command = command
        self.api_key_file = api_key_file
        self.timeout_seconds = timeout_seconds

    def available(self) -> bool:
        """检查服务端密钥文件和 CLI 命令配置是否存在。"""
        return bool(self.command.strip()) and Path(self.api_key_file).is_file()

    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """执行一次途牛工具调用并返回安全的结构化 JSON。"""
        if not self.available():
            raise TuniuProviderError("tuniu_provider_not_configured")
        try:
            api_key = Path(self.api_key_file).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise TuniuProviderError("tuniu_secret_unavailable", retryable=True) from error
        if not api_key:
            raise TuniuProviderError("tuniu_secret_empty")
        env = os.environ.copy()
        env["TUNIU_API_KEY"] = api_key
        try:
            process = await asyncio.create_subprocess_exec(
                self.command,
                "call",
                server,
                tool,
                "-a",
                json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as error:
            raise TuniuProviderError("tuniu_cli_unavailable", retryable=False) from error
        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise TuniuProviderError("tuniu_provider_timeout", retryable=True) from error
        if process.returncode != 0:
            raise TuniuProviderError(
                "tuniu_provider_call_failed", retryable=process.returncode in {7, 28, 429}
            )
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TuniuProviderError("tuniu_provider_response_invalid") from error
        if isinstance(payload, dict) and payload.get("success") is False:
            return {
                "status": "provider_error",
                "error_code": "tuniu_provider_call_failed",
                "retryable": False,
            }
        return _extract_payload(payload)


def _extract_payload(payload: Any) -> dict[str, Any]:
    """从 CLI/MCP 常见包装层中提取对象，不保留原始凭据或错误堆栈。"""
    if isinstance(payload, dict):
        structured = payload.get("structuredContent")
        if isinstance(structured, dict):
            return _extract_payload(structured)
        for wrapper in ("result", "data"):
            nested = payload.get(wrapper)
            if isinstance(nested, dict):
                return _extract_payload(nested)
        content = payload.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    try:
                        nested = json.loads(item["text"])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(nested, dict):
                        return nested
        return payload
    raise TuniuProviderError("tuniu_provider_response_invalid")


def extract_payment_url(payload: dict[str, Any]) -> str | None:
    """提取途牛返回的支付跳转地址，仅接受 HTTPS URL。"""
    for key in ("payUrl", "paymentUrl", "cashierUrl", "orderDetailH5Url", "orderDetailUrl"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith("https://"):
            return value
    return None


def extract_external_order_no(payload: dict[str, Any]) -> str | None:
    """提取外部订单号，供内部 booking_record 保存和幂等查询使用。"""
    for key in ("externalOrderNo", "orderNo", "orderId", "order_id"):
        value = payload.get(key)
        if isinstance(value, (str, int)) and str(value):
            return str(value)
    return None
