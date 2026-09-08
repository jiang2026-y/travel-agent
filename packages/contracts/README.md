# 版本化契约源码

本目录保存运行单元之间共享的版本化协议，不保存 Agent、API 或前端的共享业务实现。

- `openapi/`：浏览器与 API Server 的 OpenAPI 源码。
- `events/`：Agent 内部事件与浏览器 SSE 的 Schema 源码。
- `commands/`：API Server 调用 Agent Server 的内部命令 Schema 源码。

当前已提供可校验的 [浏览器 API OpenAPI](./openapi/travel-agent-v1.yaml)、[SSE 事件 Schema](./events/sse-event-v1.schema.json) 和 [Agent 命令 Schema](./commands/agent-command-v1.schema.json)。语义说明仍以 [API v1](../../specs/001-travel-assistant-mvp/contracts/api-v1.md)、[SSE v1](../../specs/001-travel-assistant-mvp/contracts/sse-events-v1.md)、[Agent 命令 v1](../../specs/001-travel-assistant-mvp/contracts/agent-commands-v1.md) 为准。
