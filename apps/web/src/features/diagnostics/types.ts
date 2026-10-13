// 文件职责：业务模块，聚集本文件中的提供的函数或类型定义。
// 本文件定义诊断面板使用的 Agent 能力类型。
// 定义 AgentCapability，对应 API Server 的能力清单响应。

export interface AgentCapability {
  name: string;
  display_name: string;
  status: "enabled" | "readonly" | "registered_disabled";
  intents: string[];
  tool_categories: string[];
  supports_hitl: boolean;
  supports_write: boolean;
}

