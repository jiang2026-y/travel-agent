// 文件职责：定义认证业务的用户、登录请求和认证状态类型。
// 定义 UserRole、UserProfile、LoginPayload 和 AuthState，供认证 API、状态及页面复用。
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

