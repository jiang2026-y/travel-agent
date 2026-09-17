// 文件职责：封装浏览器访问偏好设置接口的请求。
// 定义 listPreferenceOptions、getPreferences、savePreferences。

import type { PreferenceCategory, PreferenceState } from "../types/preferences";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

interface ErrorResponse {
  error?: { message?: string };
}

function csrfHeaders(): Record<string, string> {
  const match = document.cookie.match(/(?:^|;\s*)travel_agent_csrf=([^;]+)/);
  return match ? { "X-CSRF-Token": decodeURIComponent(match[1]) } : {};
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ErrorResponse;
    throw new Error(body.error?.message ?? "请求暂时无法完成，请稍后重试");
  }
  return (await response.json()) as T;
}

export async function listPreferenceOptions(): Promise<PreferenceCategory[]> {
  const response = await fetch(`${apiBaseUrl}/api/v1/preferences/options`, {
    credentials: "include",
  });
  return ((await readJson<{ categories: PreferenceCategory[] }>(response)).categories ?? []);
}

export async function getPreferences(): Promise<PreferenceState> {
  const response = await fetch(`${apiBaseUrl}/api/v1/preferences`, {
    credentials: "include",
  });
  return readJson<PreferenceState>(response);
}

export async function savePreferences(
  preferences: Record<string, string[]>,
): Promise<{ saved: boolean; count: number; message: string }> {
  const response = await fetch(`${apiBaseUrl}/api/v1/preferences`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify({ preferences }),
  });
  return readJson<{ saved: boolean; count: number; message: string }>(response);
}
