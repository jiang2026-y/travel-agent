// 文件职责：业务模块，聚集本文件中的提供的函数或类型定义。
// 本文件定义聊天业务使用的会话、Run、交互、SSE 和结果卡片类型。
// 定义 ConversationSummary、RunSummary、RunCreateResponse、ChatMessage、PendingInteraction、RunEvent 及结果卡片类型。

export type RunStatus =
  | "queued"
  | "created"
  | "running"
  | "clarifying"
  | "proposed"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "cancelled";

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  updated_at: string;
}

export interface RunDiagnosticStage {
  type: string;
  timestamp?: string;
  data: Record<string, unknown>;
}

export interface TokenUsageSummary {
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  model_calls?: number;
}

export interface RunDiagnostics {
  stages: RunDiagnosticStage[];
  route?: Record<string, unknown>;
  master?: Record<string, unknown>;
  sub_agents?: Array<Record<string, unknown>>;
  recommendation?: Record<string, unknown>;
  knowledge_search?: Record<string, unknown>;
  token_usage?: TokenUsageSummary;
  events_unavailable?: boolean;
}

export interface RunSummary {
  run_id: string;
  conversation_id: string;
  thread_id: string;
  status: RunStatus;
  pending_interaction?: PendingInteraction | null;
  assistant_reply?: string | null;
  diagnostics?: RunDiagnostics;
}

export interface RunCreateResponse {
  run_id: string;
  conversation_id: string;
  thread_id: string;
  status: "queued";
  pending_interaction: null;
}

export interface ResumeRunPayload {
  kind: "message" | "decision" | "quick_action";
  message?: string;
  interaction_id?: string;
  decision?: "approve" | "reject" | "edit" | "respond";
  confirmation_token?: string;
  edited_args?: Record<string, unknown>;
  quick_action?: string;
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
  status?: "issued" | "consumed" | "rejected" | "cancelled";
  tool_name?: string | null;
  args_hash?: string | null;
  action_version?: number;
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

