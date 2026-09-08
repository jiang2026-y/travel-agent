# 意图识别检查脚本：从 Agent 容器内部调用受保护命令并输出安全的路由结果。
# 定义消息参数，编码后交给容器内 Python 生成关联标识并发送检查请求。
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateLength(1, 8000)]
    [string]$Message,

    [ValidateLength(0, 2000)]
    [string]$ContextSummary = ""
)

$ErrorActionPreference = "Stop"
$messageBase64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Message))
$summaryBase64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($ContextSummary))

$python = @'
import base64
import json
import pathlib
import urllib.error
import urllib.request
import uuid
import os

message = base64.b64decode(os.environ["INTENT_CHECK_MESSAGE"]).decode("utf-8")
summary = base64.b64decode(os.environ["INTENT_CHECK_SUMMARY"]).decode("utf-8")
request_id = uuid.uuid4().hex
trace_id = uuid.uuid4().hex
payload = {
    "command_version": "v1",
    "command": "start",
    "request_id": request_id,
    "trace_id": trace_id,
    "user": {"user_id": "intent_check_user", "role": "user", "privacy_status": "active"},
    "conversation_id": f"intentcheck_{uuid.uuid4().hex}",
    "run_id": f"intentcheck_{uuid.uuid4().hex}",
    "thread_id": f"intentcheck_{uuid.uuid4().hex}",
    "task_brief": message,
    "context_summary": summary,
}
token = pathlib.Path("/run/secrets/api_agent_internal_token").read_text(encoding="utf-8").strip()
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "X-Request-Id": request_id,
    "X-Trace-Id": trace_id,
    "X-Run-Id": payload["run_id"],
    "X-Thread-Id": payload["thread_id"],
    "X-Internal-User-Id": "intent_check_user",
    "X-Internal-User-Role": "user",
    "X-Internal-Privacy-Status": "active",
}
request = urllib.request.Request(
    "http://127.0.0.1:8001/internal/v1/commands/runs/start",
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers=headers,
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
except urllib.error.HTTPError as error:
    detail = error.read().decode("utf-8", errors="replace")
    print(json.dumps({"status": error.code, "detail": detail}, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print(json.dumps({
    "status": result["status"],
    "routing_action": result["routing_action"],
    "target_agent": result["target_agent"],
    "intent_code": result["intent_code"],
    "intent_source": result["intent_source"],
    "diagnostics": result.get("diagnostics", {}),
    "trace_id": response.headers.get("X-Trace-Id"),
}, ensure_ascii=False, indent=2))
'@

$python | docker compose -f docker/compose.dev.yml exec -T -e "INTENT_CHECK_MESSAGE=$messageBase64" -e "INTENT_CHECK_SUMMARY=$summaryBase64" agent-server python -
