// 文件职责：定义旅行助手工作台使用的会话、Run、消息、交互和 SSE 事件类型。
// 定义 ConversationSummary、RunSummary、ChatMessage、PendingInteraction、RunEvent。

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  updated_at: string;
}

export interface RunSummary {
  run_id: string;
  conversation_id: string;
  thread_id: string;
  status: string;
  pending_interaction?: PendingInteraction | null;
  assistant_reply?: string | null;
  diagnostics?: RunDiagnostics;
}

export interface RunDiagnosticStage { type: string; timestamp?: string; data: Record<string, unknown>; }
export interface TokenUsageSummary {
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  model_calls?: number;
}

export interface RunDiagnostics { stages: RunDiagnosticStage[]; route?: Record<string, unknown>; master?: Record<string, unknown>; sub_agents?: Array<Record<string, unknown>>; recommendation?: Record<string, unknown>; knowledge_search?: Record<string, unknown>; token_usage?: TokenUsageSummary; events_unavailable?: boolean; }
export interface AgentCapability {
  name: string;
  display_name: string;
  status: "enabled" | "readonly" | "registered_disabled";
  intents: string[];
  tool_categories: string[];
  supports_hitl: boolean;
  supports_write: boolean;
}

export interface ChatMessage {
  message_id: string;
  run_id: string | null;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  feedback?: string | null;
}

export interface PendingInteraction {
  interaction_id: string;
  kind: "clarification" | "approval";
  confirmation_token?: string;
  summary: {
    question?: string;
    options?: string[];
    fields?: string[];
    message?: string;
    tool_name?: string;
    args_hash?: string;
    ui_type?: string;
  };
  allowed_decisions: Array<"approve" | "reject" | "edit" | "respond">;
}

export interface RunEvent {
  event_id?: string;
  run_id?: string;
  trace_id?: string;
  type: string;
  data: Record<string, unknown>;
  timestamp?: string;
}

export interface ResultCardItem {
  code: string;
  airline?: string;
  fromStation?: string;
  toStation?: string;
  departureTime?: string;
  arrivalTime?: string;
  duration?: string;
  cabin?: string;
  seats?: string;
  transport?: string;
  detail?: string;
  price?: string;
  priceLabel?: string;
}

export interface ResultCardPayload {
  kind: "flight" | "train" | "hotel";
  title: string;
  source: string;
  count: number;
  items: ResultCardItem[];
}

export interface AdminAuditEntry {
  event_type: string;
  actor_user_id: string | null;
  outcome: string;
  trace_id: string;
  request_id: string;
  run_id: string | null;
  thread_id: string | null;
  created_at: string;
}
