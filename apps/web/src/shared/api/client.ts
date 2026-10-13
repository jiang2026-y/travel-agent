// 本文件统一封装 Web 到 API Server 的 HTTP 请求和错误信封处理。
// 定义 ApiRequestError、apiUrl、apiFetch、requestJson、requestVoid 与 toApiError，统一处理 Base URL、Cookie 会话、CSRF 与后端错误。

import { csrfHeaders } from "./csrf";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

export interface ApiErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
  };
  trace_id?: string;
  request_id?: string;
}

export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly retryable: boolean;
  readonly traceId: string | null;
  readonly requestId: string | null;

  constructor(status: number, envelope: ApiErrorEnvelope) {
    super(envelope.error?.message ?? "请求暂时无法完成，请稍后重试");
    this.name = "ApiRequestError";
    this.status = status;
    this.code = envelope.error?.code ?? null;
    this.retryable = envelope.error?.retryable ?? false;
    this.traceId = envelope.trace_id ?? null;
    this.requestId = envelope.request_id ?? null;
  }
}

export function apiUrl(path: string): string {
  return `${apiBaseUrl}/${path.replace(/^\//, "")}`;
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") {
    for (const [key, value] of Object.entries(csrfHeaders())) headers.set(key, value);
  }
  return fetch(apiUrl(path), {
    ...init,
    headers,
    credentials: "include",
  });
}

export async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiFetch(path, init);
  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function requestVoid(path: string, init: RequestInit = {}): Promise<void> {
  await requestJson<unknown>(path, init);
}

export async function toApiError(response: Response): Promise<ApiRequestError> {
  const body = (await response.json().catch(() => ({}))) as ApiErrorEnvelope;
  return new ApiRequestError(response.status, body);
}
