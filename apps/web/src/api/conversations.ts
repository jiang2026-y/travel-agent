// 文件职责：封装浏览器访问 API Server 的会话、Run、消息和 SSE 请求。
// 定义 listConversations、startRun、getRun、resumeRun、cancelRun、listMessages、
// renameConversation、deleteConversation、streamRunEvents。

import type {
  ChatMessage,
  ConversationSummary,
  PendingInteraction,
  RunEvent,
  RunSummary,
} from "../types/conversations";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

interface ErrorResponse {
  error?: { message?: string };
}

function csrfHeaders(): Record<string, string> {
  const token = readCookie("travel_agent_csrf");
  return token ? { "X-CSRF-Token": token } : {};
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ErrorResponse;
    throw new Error(body.error?.message ?? "请求暂时无法完成，请稍后重试");
  }
  return (await response.json()) as T;
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const response = await fetch(`${apiBaseUrl}/api/v1/conversations`, { credentials: "include" });
  return ((await readJson<{ conversations: ConversationSummary[] }>(response)).conversations ?? []);
}

export async function startRun(message: string, conversationId?: string): Promise<RunSummary> {
  const response = await fetch(`${apiBaseUrl}/api/v1/conversations/runs`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(
      conversationId ? { message, conversation_id: conversationId } : { message },
    ),
  });
  return readJson<RunSummary>(response);
}

export async function getRun(runId: string): Promise<RunSummary> {
  const response = await fetch(`${apiBaseUrl}/api/v1/runs/${encodeURIComponent(runId)}`, {
    credentials: "include",
  });
  return readJson<RunSummary>(response);
}

export async function resumeRun(
  runId: string,
  payload: {
    kind: "message" | "decision" | "quick_action";
    message?: string;
    interaction_id?: string;
    decision?: "approve" | "reject" | "edit" | "respond";
    confirmation_token?: string;
    quick_action?: string;
  },
): Promise<RunSummary> {
  const response = await fetch(`${apiBaseUrl}/api/v1/runs/${encodeURIComponent(runId)}/resume`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(payload),
  });
  return readJson<RunSummary>(response);
}

export async function cancelRun(runId: string): Promise<RunSummary> {
  const response = await fetch(`${apiBaseUrl}/api/v1/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    credentials: "include",
    headers: csrfHeaders(),
  });
  return readJson<RunSummary>(response);
}

export async function listMessages(conversationId: string): Promise<ChatMessage[]> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`,
    { credentials: "include" },
  );
  return ((await readJson<{ messages: ChatMessage[] }>(response)).messages ?? []);
}

export async function renameConversation(
  conversationId: string,
  title: string,
): Promise<{ conversation_id: string; title: string }> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/conversations/${encodeURIComponent(conversationId)}/title`,
    {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      body: JSON.stringify({ title }),
    },
  );
  return readJson<{ conversation_id: string; title: string }>(response);
}

export async function deleteConversation(
  conversationId: string,
): Promise<{ conversation_id: string; status: string }> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/conversations/${encodeURIComponent(conversationId)}`,
    { method: "DELETE", credentials: "include", headers: csrfHeaders() },
  );
  return readJson<{ conversation_id: string; status: string }>(response);
}

export async function setMessageFeedback(
  conversationId: string,
  messageId: string,
  feedback: "up" | "down" | null,
): Promise<{ message_id: string; feedback: string | null }> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/feedback`,
    {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      body: JSON.stringify({ feedback }),
    },
  );
  return readJson<{ message_id: string; feedback: string | null }>(response);
}

export async function streamRunEvents(
  runId: string,
  lastEventId: string | null,
  onEvent: (event: RunEvent, eventId: string | null) => void,
  signal: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = {};
  if (lastEventId) headers["Last-Event-ID"] = lastEventId;
  const response = await fetch(`${apiBaseUrl}/api/v1/runs/${encodeURIComponent(runId)}/events`, {
    credentials: "include",
    headers,
    signal,
  });
  if (!response.ok || !response.body) {
    await readJson(response);
    return;
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
      // 丢弃无法解析的事件，等待下一次状态查询兜底。
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
      if (!line) {
        flush();
      } else if (line.startsWith("event:")) {
        eventName = line.slice(6).trim() || "message";
      } else if (line.startsWith("id:")) {
        eventId = line.slice(3).trim() || null;
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
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

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie.split("; ").find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : null;
}
