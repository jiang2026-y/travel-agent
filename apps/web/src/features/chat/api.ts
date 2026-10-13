// 文件职责：业务模块，聚集本文件中的提供的函数或类型定义。
// 本文件封装聊天业务的会话、Run、消息、反馈和 SSE 请求。
// 定义会话生命周期 API、HITL 恢复 API、SSE 解析器和 interactionQuestion 辅助函数。

import { ApiRequestError, apiFetch, requestJson } from "../../shared/api/client";
import type {
  ChatMessage,
  ConversationSummary,
  PendingInteraction,
  ResumeRunPayload,
  RunCreateResponse,
  RunEvent,
  RunSummary,
} from "./types";

interface ErrorEnvelope {
  error?: { code?: string; message?: string; retryable?: boolean };
  trace_id?: string;
  request_id?: string;
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const body = await requestJson<{ conversations: ConversationSummary[] }>(
    "/api/v1/conversations",
  );
  return body.conversations ?? [];
}

export async function startRun(
  message: string,
  conversationId?: string,
): Promise<RunCreateResponse> {
  return requestJson<RunCreateResponse>("/api/v1/conversations/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(
      conversationId ? { message, conversation_id: conversationId } : { message },
    ),
  });
}

export async function getRun(runId: string): Promise<RunSummary> {
  return requestJson<RunSummary>(`/api/v1/runs/${encodeURIComponent(runId)}`);
}

export async function resumeRun(
  runId: string,
  payload: ResumeRunPayload,
): Promise<RunSummary> {
  return requestJson<RunSummary>(`/api/v1/runs/${encodeURIComponent(runId)}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function cancelRun(runId: string): Promise<RunSummary> {
  return requestJson<RunSummary>(`/api/v1/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
  });
}

export async function listMessages(conversationId: string): Promise<ChatMessage[]> {
  const body = await requestJson<{ messages: ChatMessage[] }>(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`,
  );
  return body.messages ?? [];
}

export async function renameConversation(
  conversationId: string,
  title: string,
): Promise<{ conversation_id: string; title: string }> {
  return requestJson<{ conversation_id: string; title: string }>(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}/title`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    },
  );
}

export async function deleteConversation(
  conversationId: string,
): Promise<{ conversation_id: string; status: string }> {
  return requestJson<{ conversation_id: string; status: string }>(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}`,
    { method: "DELETE" },
  );
}

export async function setMessageFeedback(
  conversationId: string,
  messageId: string,
  feedback: "up" | "down" | null,
): Promise<{ message_id: string; feedback: string | null }> {
  return requestJson<{ message_id: string; feedback: string | null }>(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/feedback`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ feedback }),
    },
  );
}

export async function streamRunEvents(
  runId: string,
  lastEventId: string | null,
  onEvent: (event: RunEvent, eventId: string | null) => void,
  signal: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = {};
  if (lastEventId) headers["Last-Event-ID"] = lastEventId;
  const response = await apiFetch(`/api/v1/runs/${encodeURIComponent(runId)}/events`, {
    headers,
    signal,
  });
  if (!response.ok || !response.body) {
    const body = (await response.json().catch(() => ({}))) as ErrorEnvelope;
    throw new ApiRequestError(response.status, body);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "message";
  let eventId: string | null = null;
  let dataLines: string[] = [];

  const flush = () => {
    if (!dataLines.length) return;
    try {
      const data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
      onEvent({ type: eventName, data }, eventId);
    } catch {
      // 丢弃无法解析的事件，下一次轮询会提供状态兜底。
    }
    eventName = "message";
    eventId = null;
    dataLines = [];
  };

  while (true) {
    const chunk = await reader.read();
    buffer += decoder.decode(chunk.value ?? new Uint8Array(), { stream: !chunk.done });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const rawLine of lines) {
      const line = rawLine.replace(/\r$/, "");
      if (!line) flush();
      else if (line.startsWith("event:")) eventName = line.slice(6).trim() || "message";
      else if (line.startsWith("id:")) eventId = line.slice(3).trim() || null;
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (chunk.done) {
      flush();
      break;
    }
  }
}

export function interactionQuestion(interaction: PendingInteraction | null | undefined): string {
  return interaction?.summary.question ?? interaction?.summary.message ?? "请补充必要信息";
}

