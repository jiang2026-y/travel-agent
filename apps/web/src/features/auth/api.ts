// 文件职责：业务模块，聚集本文件中的提供的函数或类型定义。
// 本文件封装认证业务对 API Server 的请求。
// 定义 login、getCurrentUser 和 logout，使用 Cookie 会话与统一错误处理。

import { apiFetch, requestJson, toApiError } from "../../shared/api/client";
import type { LoginPayload, UserProfile } from "./types";

interface UserResponse {
  user: UserProfile;
}

export async function login(payload: LoginPayload): Promise<UserProfile> {
  const response = await requestJson<UserResponse>("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.user;
}

export async function getCurrentUser(): Promise<UserProfile | null> {
  const response = await apiFetch("/api/v1/auth/me");
  if (response.status === 401) return null;
  if (!response.ok) throw await toApiError(response);
  return ((await response.json()) as UserResponse).user;
}

export async function logout(): Promise<void> {
  await requestJson<unknown>("/api/v1/auth/logout", { method: "POST" });
}

