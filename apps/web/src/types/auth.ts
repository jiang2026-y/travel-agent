// 本文件定义前端认证领域类型；定义 UserProfile、LoginPayload 与 AuthState，供 API、状态和页面复用。

export type UserRole = "user" | "admin";

export interface UserProfile {
  user_id: string;
  account: string;
  role: UserRole;
}

export interface LoginPayload {
  account: string;
  password: string;
}

export interface AuthState {
  user: UserProfile | null;
  initialized: boolean;
  loading: boolean;
  error: string | null;
}
