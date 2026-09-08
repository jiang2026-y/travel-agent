// 本文件定义前端应用根组件；定义 App，用于挂载受保护路由并隔离页面与认证状态。

import { AppRouter } from "../router";

export function App() {
  // 挂载基于服务端 Cookie 会话的页面路由。
  return (
    <AppRouter />
  );
}
