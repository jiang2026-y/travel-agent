# Docker 本地启动说明

本目录集中存放本项目的 Docker 启动文件。开发环境采用 `real_readonly`；Compose 会启动 Web、API Server、Agent Server、受限 Tool Gateway、默认拒绝的 Egress Proxy、PostgreSQL 和 Redis。未登记经审批的 Provider 时，Tool Gateway 与 Egress Proxy 默认拒绝所有外部调用；不会创建业务 Schema、执行迁移或连接真实百炼、短信、旅行供应商。

前端使用 `pnpm 10.34.5`，以兼容当前 Node 20.20.2 开发运行时；Python 服务使用 `uv` 管理的 Python 3.12.x。

## 启动

首次启动前，必须按 [`.secrets/README.md`](../.secrets/README.md) 在本机生成 API-Agent 与 Agent-Tool Gateway 两个内部 Token，并将轮换后的百炼 API Key 保存为 `.secrets/dashscope_api_key`。这些文件均由 Compose 以 Docker Secret 只读挂载，不能提交、写入环境变量或打印到日志。

在仓库根目录执行：

```powershell
docker compose -f docker/compose.dev.yml up --build
```

启动后访问：

- Web：`http://localhost:5173`
- API 健康检查：`http://localhost:8000/health`
- Agent 健康检查：Agent Server 不发布宿主机端口；使用 `docker compose -f docker/compose.dev.yml exec agent-server python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/health').read().decode())"` 在容器内检查。

Tool Gateway 不映射宿主机端口，只能由 Agent Server 经内部 Docker 网络访问。该容器以非 root 用户、只读根文件系统、最小 Linux 能力、独立 PID/IPC/网络命名空间和资源限制运行；不会挂载 Docker Socket、宿主机目录或凭据目录。

边缘网络仅承载 Web 与 API Server 的宿主机端口发布；API、Agent、PostgreSQL 与 Redis 使用内部后端网络；Agent Server 经 `tool-gateway-network` 访问 Tool Gateway，并经 `egress-network` 直连公网——后者是 `tuniu` CLI 访问 `openapi.tuniu.cn` 的必要条件，缺少该网络时容器内 DNS 直接失败，途牛查询、下单与取消都会降级。Egress Proxy 只允许访问已确认的北京地域百炼工作空间域名、免费天气域名 `wttr.in`、资讯域名 `newsdata.io`、百炼长期记忆域名 `dashscope.aliyuncs.com`、百炼知识库检索域名 `bailian.cn-beijing.aliyuncs.com` 与签证域名 `visa.orizn.app` 的 HTTPS CONNECT；Tool Gateway 在这些边界内转发 `text-embedding-v4`（1024 维）、`glm-5.1`、`qwen3.7-plus`、`qwen3.6-flash` 的只读推理，以及知识库检索、目的地天气、目的地资讯、签证查询与长期记忆读写。意图种子以每批最多 10 条的 embedding 请求初始化，68 条种子会发出 7 个请求；旅行供应商写操作仍需用户确认与内部 Token。

聊天推理按 `X-Agent-Name` 分组校验工具白名单：`qwen3.7-plus` 只放行 `masterAgent`、`bookingAgent` 与 `itineraryManageAgent` 的工具，`glm-5.1` 仅在 `infoAgent` 名下放行信息类工具，`qwen3.6-flash` 继续禁止工具与思考模式。未携带 Agent 名或超出白名单的工具调用会被 422 拒绝。

以下能力全部为可选，对应 Docker Secret 缺失时自动降级：目的地资讯（`newsdata_api_key`）、更长期天气 MCP（`weather_mcp_endpoint`）、百炼长期记忆（只需 `bailian_memory_library_id`，复用 `dashscope_api_key`，接口为百炼记忆库 v2 的 `/api/v2/apps/memory/add` 与 `/api/v2/apps/memory/memory_nodes`；详见 `.secrets/README.md`）、Orizn 签证（`orizn_visa_api_key`，缺失时仅免 Key 的快速检查与覆盖统计可用）。

## 运行中断与检查点

### 工具熔断（开发环境已启用）

外部依赖型工具可启用按工具的熔断降级：连续失败达到阈值（默认 3 次）后进入 OPEN，冷却期内直接返回降级提示、不再发起外部调用，冷却结束自动半开探测，失败按指数退避（60s → 120s → … 上限 600s）重新熔断，成功则完全恢复。状态保存在 Redis（键前缀 `tool:cb:`），与 Java `ToolCircuitBreakerHook` 语义一致；Redis 不可用时退回进程内状态并只记 warning。

开发环境已开启（`TRAVEL_AGENT_TOOL_CIRCUIT_BREAKER_ENABLED: "true"`），监控 `query_weather`、`query_destination_news`、`quick_visa_check`、`check_visa_requirement`；如需退回 Java 默认的两项白名单，把 `TRAVEL_AGENT_TOOL_CIRCUIT_MONITORED_TOOLS` 改为 `query_weather,query_destination_news`。

### 会话功能面与偏好设置

- **运行进展**：Agent Server 发布 `tool_started` / `tool_completed`（仅工具名、状态、耗时，不含参数与正文），配合既有 `intent_recognition`、`query_rewrite`、`route_selected`、`sub_agent_*`、`knowledge_search_*`、`token_usage` 事件，前端渲染成"跑到哪一步"的时间线。
- **偏好设置**：`GET /api/v1/preferences/options` 返回四类选项目录（机票/酒店/高铁/出行习惯）；`GET /api/v1/preferences` 经 Agent Server 召回百炼长期记忆并用 qwen3.6-flash 解析为结构化勾选；`POST /api/v1/preferences` 把勾选格式化为中文偏好句子写入记忆。记忆未配置时返回 `available=false` 并降级。
- **会话功能**：`DELETE /api/v1/conversations/{id}`（软删除）、`PUT /api/v1/conversations/{id}/title`（改名）、`PUT /api/v1/conversations/{id}/messages/{mid}/feedback`（点赞/点踩，复用既有 `messages.feedback` 列，无需迁移）。
- **管理员**：`GET /api/v1/admin/approvals`、`POST /api/v1/admin/approvals/{pid}/decision`、`GET /api/v1/admin/debug/agents`、`POST /api/v1/admin/debug/agents/{name}`（调试直达绕过意图识别与主控）。

- Agent Server 为每个会话登记在途运行（`orchestration/execution.py`）；`cancel` 命令即"停止生成"语义：标记检查点 `cancelled`、取消本地在途任务、清理待交互并广播中断。
- 跨节点中断通过 Redis Pub/Sub 频道 `agent:interrupt` 传播，消息为 `conversation_id|timestamp_ms`；收到广播的节点只取消早于该时刻登记的运行，避免误杀之后启动的新执行流。
- 同一会话发起新消息时会先自动打断上一轮在途运行（对齐 Java `interruptPrevious`）；被中断的运行保留最后一个检查点，用户可继续对话恢复。
- API Server 对内部命令做超时分层：执行类（`start`/`resume`/`interrupt`）使用 `TRAVEL_AGENT_RUN_COMMAND_TIMEOUT_SECONDS`（默认 600 秒），查询类固定 5 秒；`resume` 与 `start` 一样异步受理，浏览器只等待 202。
- API Server 启动时会把超过 24 小时仍处于 `created`/`queued`/`running` 的 Run 标记为 `failed` 并写审计事件 `stale_runs_failed`。

## 停止

```powershell
docker compose -f docker/compose.dev.yml down
```

如需删除本地开发数据卷，必须先确认目标环境和数据可删除性；本阶段不执行删除卷操作。
