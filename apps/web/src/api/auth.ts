// 本文件封装浏览器到 API Server 的认证请求；定义登录、当前会话和退出函数，不读取或存储会话 Token。

import type { LoginPayload, UserProfile } from "../types/auth";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

interface UserResponse {
  user: UserProfile;
}

interface ErrorResponse {
  error?: {
    message?: string;
  };
}

export async function login(payload: LoginPayload): Promise<UserProfile> {
  // 提交账号密码；会话仅由浏览器 Cookie 保存。
  const response = await fetch(`${apiBaseUrl}/api/v1/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readUserResponse(response);
}

export async function getCurrentUser(): Promise<UserProfile | null> {
  // 读取当前服务端会话；未登录时返回空而非抛出页面级异常。
  const response = await fetch(`${apiBaseUrl}/api/v1/auth/me`, { credentials: "include" });
  if (response.status === 401) {
    return null;
  }
  return readUserResponse(response);
}

export async function logout(): Promise<void> {
  // 发送 CSRF 请求头退出服务端会话；前端不访问 HttpOnly 会话 Cookie。
  const response = await fetch(`${apiBaseUrl}/api/v1/auth/logout`, {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": readCookie("travel_agent_csrf") ?? "" },
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
}

async function readUserResponse(response: Response): Promise<UserProfile> {
  // 解析成功用户响应或转换为安全的用户提示。
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return (await response.json() as UserResponse).user;
}

async function readErrorMessage(response: Response): Promise<string> {
  // 从安全错误信封提取面向用户的消息，不展示服务端堆栈。
  const body = await response.json().catch(() => ({})) as ErrorResponse;
  return body.error?.message ?? "请求暂时无法完成，请稍后重试";
}

function readCookie(name: string): string | null {
  // 读取仅用于 Double Submit 校验的非 HttpOnly CSRF Cookie。
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie.split("; ").find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : null;
}
