// 本文件声明 Vite 前端环境变量类型；定义 ImportMetaEnv 和 ImportMeta 接口扩展。

/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
