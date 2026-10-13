// 文件职责：根据服务端 Cookie 会话切换登录页与工作台。
// 定义 AppRouter，负责初始化认证状态并渲染应用页面。

import { Spin } from "antd";
import { useEffect } from "react";

import { LoginPage } from "../features/auth/LoginPage";
import { useAuthStore } from "../features/auth/auth-store";
import { WorkbenchPage } from "../features/chat/pages/WorkbenchPage";

export function AppRouter() {
  const { initialized, initialize, user } = useAuthStore();

  useEffect(() => {
    void initialize();
  }, [initialize]);

  if (!initialized) {
    return (
      <div className="page-loading">
        <Spin size="large" />
      </div>
    );
  }
  return user ? <WorkbenchPage /> : <LoginPage />;
}
