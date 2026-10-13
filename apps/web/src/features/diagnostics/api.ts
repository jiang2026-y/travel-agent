// 文件职责：业务模块，聚集本文件中的提供的函数或类型定义。
// 本文件封装诊断业务的 Agent 能力清单接口。
// 定义 listAgentCapabilities，读取经过 API Server 过滤的能力描述。

import { requestJson } from "../../shared/api/client";
import type { AgentCapability } from "./types";

export async function listAgentCapabilities(): Promise<AgentCapability[]> {
  const body = await requestJson<{ agents: AgentCapability[] }>(
    "/api/v1/agent-capabilities",
  );
  return body.agents ?? [];
}

