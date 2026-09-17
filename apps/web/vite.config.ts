// 本文件配置 Vite 前端开发与构建行为；定义默认导出的 Vite 配置对象。

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    // Docker Desktop 的 Windows 挂载点收不到 inotify 事件，必须轮询才能热更新。
    watch: {
      usePolling: true,
      interval: 300,
    },
  },
});
