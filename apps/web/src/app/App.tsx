// 文件职责：定义前端应用根组件并挂载应用壳路由。
// 定义 App，负责渲染 AppRouter。

import { AppRouter } from "./router";

export function App() {
  return <AppRouter />;
}
