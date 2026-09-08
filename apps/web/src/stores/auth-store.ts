// 本文件定义前端认证状态；定义 useAuthStore，用于初始化会话、登录、退出和错误状态管理。

import { create } from "zustand";

import * as authApi from "../api/auth";
import type { AuthState, LoginPayload } from "../types/auth";

interface AuthActions {
  initialize: () => Promise<void>;
  login: (payload: LoginPayload) => Promise<void>;
  logout: () => Promise<void>;
  clearError: () => void;
}

export const useAuthStore = create<AuthState & AuthActions>((set) => ({
  user: null,
  initialized: false,
  loading: false,
  error: null,
  initialize: async () => {
    set({ loading: true, error: null });
    try {
      const user = await authApi.getCurrentUser();
      set({ user, initialized: true, loading: false });
    } catch {
      set({ user: null, initialized: true, loading: false, error: "会话状态读取失败，请刷新后重试" });
    }
  },
  login: async (payload) => {
    set({ loading: true, error: null });
    try {
      const user = await authApi.login(payload);
      set({ user, loading: false });
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : "登录失败" });
    }
  },
  logout: async () => {
    set({ loading: true, error: null });
    try {
      await authApi.logout();
      set({ user: null, loading: false });
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : "退出失败" });
    }
  },
  clearError: () => set({ error: null }),
}));
