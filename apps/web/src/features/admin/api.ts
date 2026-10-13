// 文件职责：业务模块，聚集本文件中的提供的函数或类型定义。
// 本文件封装管理员状态、审批、审计和 Agent 调试接口。
// 定义管理员业务的查询和写入函数，所有写请求通过统一客户端携带 CSRF。

import { requestJson } from "../../shared/api/client";
import type { AdminAuditEntry, ApprovalRecord, DebugAgent } from "./types";

export async function getAdminStatus(): Promise<{ role: string; status: string }> {
  return requestJson<{ role: string; status: string }>("/api/v1/admin/status");
}

export async function listAuditEntries(): Promise<AdminAuditEntry[]> {
  const body = await requestJson<{ entries: AdminAuditEntry[] }>("/api/v1/admin/audit");
  return body.entries ?? [];
}

export async function listApprovals(status?: string): Promise<ApprovalRecord[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const body = await requestJson<{ approvals: ApprovalRecord[] }>(
    `/api/v1/admin/approvals${query}`,
  );
  return body.approvals ?? [];
}

export async function decideApproval(
  processInstanceId: string,
  decision: "approve" | "reject",
  remark?: string,
): Promise<ApprovalRecord> {
  return requestJson<ApprovalRecord>(
    `/api/v1/admin/approvals/${encodeURIComponent(processInstanceId)}/decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, remark }),
    },
  );
}

export async function listDebugAgents(): Promise<DebugAgent[]> {
  const body = await requestJson<{ agents: DebugAgent[] }>("/api/v1/admin/debug/agents");
  return body.agents ?? [];
}

export async function debugAgent(
  agentName: string,
  message: string,
): Promise<{ agent: string; assistant_reply: string; pending_interaction?: unknown }> {
  return requestJson<{ agent: string; assistant_reply: string; pending_interaction?: unknown }>(
    `/api/v1/admin/debug/agents/${encodeURIComponent(agentName)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    },
  );
}

