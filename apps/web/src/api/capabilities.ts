// 文件职责：读取经过认证的 Agent 能力清单；定义 listAgentCapabilities 请求函数。
import type { AgentCapability } from "../types/conversations";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export async function listAgentCapabilities(): Promise<AgentCapability[]> {
  const response = await fetch(`${apiBaseUrl}/api/v1/agent-capabilities`, { credentials: "include" });
  if (!response.ok) throw new Error("Agent 能力清单读取失败");
  return ((await response.json()) as { agents?: AgentCapability[] }).agents ?? [];
}
