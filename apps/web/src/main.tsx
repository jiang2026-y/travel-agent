// 本文件挂载 React 前端应用；定义 renderApp 调用，用于将 App 渲染到浏览器根节点。

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "antd/dist/reset.css";

import { App } from "./app/App";
import "./styles/global.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
