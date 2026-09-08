// 本文件定义前端最小受保护路由入口；定义 AppRouter，用于根据服务端 Cookie 会话切换登录页与工作台。

import { useEffect } from "react";
import { Spin } from "antd";

import { LoginPage } from "../pages/LoginPage";
import { WorkbenchPage } from "../pages/WorkbenchPage";
import { useAuthStore } from "../stores/auth-store";

export function AppRouter() {
  // 初始化当前会话，并在加载、未登录、已登录三种状态间安全切换。
  const { initialized, initialize, user } = useAuthStore();

  useEffect(() => {
    void initialize();
  }, [initialize]);

  if (!initialized) {
    return <div className="page-loading"><Spin size="large" /></div>;
  }
  return user ? <WorkbenchPage /> : <LoginPage />;
}
