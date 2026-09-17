// 文件职责：封装管理员状态、脱敏审计、审批决策与调试直达接口。
// 定义 getAdminStatus、listAuditEntries、listApprovals、decideApproval、
// listDebugAgents 与 debugAgent。

import type { AdminAuditEntry } from "../types/conversations";
import type { ApprovalRecord, DebugAgent } from "../types/preferences";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export async function getAdminStatus(): Promise<{ role: string; status: string }> {
  const response = await fetch(`${apiBaseUrl}/api/v1/admin/status`, { credentials: "include" });
  return readJson(response);
}

export async function listAuditEntries(): Promise<AdminAuditEntry[]> {
  const response = await fetch(`${apiBaseUrl}/api/v1/admin/audit`, { credentials: "include" });
  const body = await readJson<{ entries: AdminAuditEntry[] }>(response);
  return body.entries ?? [];
}

function csrfHeaders(): Record<string, string> {
  const match = document.cookie.match(/(?:^|;\s*)travel_agent_csrf=([^;]+)/);
  return match ? { "X-CSRF-Token": decodeURIComponent(match[1]) } : {};
}

export async function listApprovals(status?: string): Promise<ApprovalRecord[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const response = await fetch(`${apiBaseUrl}/api/v1/admin/approvals${query}`, {
    credentials: "include",
  });
  const body = await readJson<{ approvals: ApprovalRecord[] }>(response);
  return body.approvals ?? [];
}

export async function decideApproval(
  processInstanceId: string,
  decision: "approve" | "reject",
  remark?: string,
): Promise<ApprovalRecord> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/admin/approvals/${encodeURIComponent(processInstanceId)}/decision`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      body: JSON.stringify({ decision, remark }),
    },
  );
  return readJson<ApprovalRecord>(response);
}

export async function listDebugAgents(): Promise<DebugAgent[]> {
  const response = await fetch(`${apiBaseUrl}/api/v1/admin/debug/agents`, {
    credentials: "include",
  });
  const body = await readJson<{ agents: DebugAgent[] }>(response);
  return body.agents ?? [];
}

export async function debugAgent(
  agentName: string,
  message: string,
): Promise<{ agent: string; assistant_reply: string }> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/admin/debug/agents/${encodeURIComponent(agentName)}`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      body: JSON.stringify({ message }),
    },
  );
  return readJson<{ agent: string; assistant_reply: string }>(response);
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error("管理员数据读取失败");
  return (await response.json()) as T;
}
