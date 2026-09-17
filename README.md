# 企业级智能差旅助手（Python 多智能体）

面向企业差旅场景的多智能体大模型应用：从"我要申请 9 月 18 日从北京去杭州出差"这样的自然语言出发，完成**差旅申请与审批、机票/火车/酒店查询与预订、差旅政策与知识问答、签证/天气/目的地资讯**等全流程动作，并保证写操作可控、过程可见、失败可降级。服务端 130 个源码文件约 2.08 万行，测试 75 个文件约 8 千行。

> **状态说明**：当前处于 MVP 迭代阶段，本地 Docker Compose 环境可完整运行并通过门禁（300 个测试通过 / mypy strict 0 错误），**尚未在生产环境上线**；模型与第三方能力均需自备密钥。

## 目录

- [一、它能做什么](#一它能做什么)
- [二、架构总览](#二架构总览)
- [三、智能体与工具](#三智能体与工具)
- [四、关键机制与参数](#四关键机制与参数)
- [五、技术栈](#五技术栈)
- [六、目录结构](#六目录结构)
- [七、快速开始](#七快速开始)
- [八、配置与密钥](#八配置与密钥)
- [九、质量门禁与数据](#九质量门禁与数据)
- [十、已知限制与后续计划](#十已知限制与后续计划)
- [十一、文档索引](#十一文档索引)

## 一、它能做什么

| 场景 | 用户输入示例 | 系统行为 |
| --- | --- | --- |
| 差旅申请 | "我要申请 9 月 18 日从北京去杭州出差，参加客户拜访，20 号返回" | 意图识别 → 冲突检查 → 生成摘要 → **弹确认卡片** → 用户确认后提交差旅单与审批单 |
| 冲突检查 | "帮我看看这趟会不会和已有行程冲突" | 按同一员工时间重叠、跨城衔接、路径断裂规则给出 HIGH/MEDIUM/LOW 事实与建议 |
| 行程/审批查询 | "我最近的行程单"、"审批到哪一步了" | 只读查询本人差旅单、审批实例与预订记录 |
| 预订 | "查一下北京到上海的航班"、"就订第一个" | 转预订智能体查询/下单，下单前强制校验已审批差旅单 |
| 知识问答 | "三亚有什么值得去的景点" | 知识库检索（百炼 Retrieve）+ 政策/天气/资讯工具 |
| 签证与政策 | "中国护照去日本要签证吗" | 6 个签证工具 + 出网白名单，未配置密钥时优雅降级 |
| 偏好记忆 | 偏好设置页勾选舱位/航司/酒店品牌 | 写入百炼长期记忆；下次对话按用户偏好回答 |

**本轮明确不做**：行程规划子智能体、行程审核子智能体、报销子智能体、SaToken 鉴权（沿用现有 Cookie + CSRF + 内部 Token 体系）。

## 二、架构总览

```
浏览器 (React 19 + Vite, :5173)
        │  Cookie 会话 + CSRF + SSE
        ▼
API Server (FastAPI, :8000) ──────── PostgreSQL 16 (:5432)
  会话 / Run / 消息 / 差旅单 / 审批 / 偏好 / 审计      Redis 8 (:6379)
        │  内部命令（Bearer Token + 调用链校验）        检查点 / HITL / 熔断 / 事件
        ▼
Agent Server (LangGraph, :8001)
  意图识别 → 路由 → 主控 Agent ⇄ 子智能体（行程管理 / 预订 / 信息）
        │
        ▼
Tool Gateway (:8002) ──── Egress Proxy (Squid, :3128，默认拒绝 + 域名白名单) ──── 外部 Provider
  模型调用白名单 / 工具白名单 / 熔断 / 流式透传
```

| 服务 | 职责 | 端口 |
| --- | --- | --- |
| `web` | 对话界面、执行过程时间线、结果卡片、偏好/我的差旅/管理面板 | 5173 |
| `api-server` | 会话与 Run 生命周期、差旅单与审批持久化、偏好、审计、SSE 转发 | 8000 |
| `agent-server` | 多智能体编排、工具实现、HITL 与中断、记忆与摘要、事件发布 | 8001（仅内网） |
| `tool-gateway` | 模型调用与工具出口，按「模型 × Agent」白名单放行，流式透传 | 8002（仅内网） |
| `egress-proxy` | 出网白名单代理，未配置的域名一律拒绝 | 3128（仅内网） |
| `db-migrate` | Alembic 一次性迁移（4 个迁移脚本） | — |

## 三、智能体与工具

主控负责路由与协调，三个子智能体各自可调用一组工具；共 **42 个工具**，按智能体分组在网关侧做白名单校验。

| 智能体 | 模型 | 主要能力 |
| --- | --- | --- |
| 主控 `masterAgent` | qwen3.7-plus（非思考） | 意图理解、子智能体调度、澄清提问（`ask_user`）、长期记忆读写 |
| 行程管理 `itineraryManageAgent` | qwen3.7-plus（非思考） | 差旅单提交/取消/修改、**冲突检查**、审批状态、差旅政策、用户档案与常驻地 |
| 预订执行 `bookingAgent` | qwen3.7-plus（非思考） | 途牛机票/火车/酒店查询与下单、取消预订、用户级 API Key 管理、技能懒加载与受控 shell |
| 信息问答 `infoAgent` | glm-5.1（工具通道需携带 Agent 标识） | 知识库检索、差旅政策、天气、目的地资讯、6 个签证工具 |

辅助小模型：`qwen3.6-flash` 负责**会话标题**与**推荐追问**（温度 0、禁工具、禁思考）；`glm-5.1` 负责**记忆压缩摘要**（温度 0、禁工具）。

意图识别分三层：L1 规则命中 → L2 向量召回 → L3 大模型判定，识别结果必须与意图目录声明的目标智能体一致，否则降级为 `unknown`，并用契约测试锁定"提示词映射表 ↔ 代码目录"的一致性。

## 四、关键机制与参数

**1. 写操作 HITL（人在环）确认**

- 写类工具触发确认卡片，Redis 只保存确认 Token 的 SHA-256 哈希，TTL 900 秒；
- 前端轮询会轮换 Token，服务端对最近一版做宽限，解决"点确认时刚好过期"的竞态；
- 写事务执行前用 `WATCH/MULTI` **原子消费** Token，并绑定「交互 ID + 工具参数指纹」，防止确认后替换参数；
- 用户提交决定后立刻摘除展示索引（保留记录供消费），避免恢复执行期间卡片反复弹出。

**2. 真可中断 + 断点续跑**

- 会话级执行注册表记录在途 `asyncio.Task`，同一会话同时只允许一个在途运行；
- 通过 Redis Pub/Sub 广播中断到其他节点，并用广播时间戳做时序校验，避免误杀广播后新起的执行流；
- 取消不写 `failed`，只发布 `interrupted` 事件，LangGraph 检查点停在上一个节点边界，用户继续发消息即可续跑；发新消息会自动打断上一轮。

**3. 流式输出与过程可视化**

- 模型侧 SSE 透传 → LangGraph `astream` 过滤工具调用增量、按 24 字符合批推送 `token` 事件；
- 共 **32 类 SSE 事件**（意图识别、路由、子智能体、工具开始/完成、知识库检索、结果卡片、Token 用量、中断等），前端把本轮事件渲染成**逐条揭示的执行时间线**；
- 途牛航班/火车/酒店查询结果渲染为结构化结果卡片（最多 5 条），原始事件仅作为管理员诊断展示。

**4. 上下文与记忆**

- 工具结果压缩：超长列表截断为 10 条并标注省略条数，丢弃模型决策无用字段，折叠过期技能正文；
- 记忆压缩：消息 ≥60 条或估算 token ≥98304 触发摘要，保留最近 20 条（`SummarizationMiddleware`，摘要模型 glm-5.1）；
- 长期记忆：百炼记忆库按 `user_id` 隔离，写入/召回经 Tool Gateway，未配置时降级为"无长期记忆"。

**5. 稳定性与降级**

- 工具级熔断：连续 3 次失败进入 OPEN，冷却 60 秒起指数退避至最多 600 秒，状态放 Redis 供多实例共享，半开探测成功自动恢复；
- 超时分层：执行类内部命令 600 秒、查询类 5 秒；启动时把超过 24 小时仍处于 `created`/`queued`/`running` 的历史 Run 标记为失败并写审计；
- 外部能力统一降级：Provider 未配置、上游 401/403/超时/5xx 都映射为稳定错误码，不向 Agent 透传上游正文。

**6. 安全与合规**

- 用户证件、手机号、姓名拼音与消息正文使用 **AES-256-GCM** 加密存储；
- 内部服务 Token、第三方 Key 全部经 Docker Secret 只读挂载，环境变量只保存文件路径；
- 出网白名单：`ws-afyh9lpghkjx1iz8.cn-beijing.maas.aliyuncs.com`（模型）、`bailian.cn-beijing.aliyuncs.com`（知识库）、`dashscope.aliyuncs.com`（长期记忆）、`wttr.in`（天气）、`newsdata.io`（资讯）、`visa.orizn.app`（签证），其余一律拒绝；
- 日志、工具输出与诊断事件统一脱敏，全链路审计事件落库；用户级第三方 API Key 只向界面暴露"是否已配置"，明文仅在内部注入 CLI 且不记录。

## 五、技术栈

| 层次 | 选型 |
| --- | --- |
| 智能体 | LangGraph / LangChain（Agent 中间件、结构化工具、检查点） |
| 后端 | Python 3.12、FastAPI、Pydantic v2、SQLModel、SQLAlchemy、Alembic |
| 存储 | PostgreSQL 16（11 张业务表）、Redis 8（检查点 / HITL / 熔断 / 事件 / 中断广播） |
| 模型 | DashScope 兼容模式：qwen3.7-plus、glm-5.1、qwen3.6-flash；百炼知识库检索与长期记忆 |
| 前端 | React 19、TypeScript 5.9、Vite 7、Ant Design 6、zustand、react-markdown + GFM |
| 工程 | uv 工作区、pnpm 工作区、Docker Compose、pytest、mypy（strict）、ruff |

## 六、目录结构

```
travel-agent/
├── apps/web/                     前端工作台（对话 / 我的差旅 / 偏好设置 / 管理）
├── services/
│   ├── agent-server/             多智能体、工具、编排、提示词、技能
│   │   └── src/travel_agent_agent/
│   │       ├── agents/{master,itinerary_manage,booking,info,common}
│   │       ├── orchestration/    检查点、HITL 交互、执行注册表、路由、状态机
│   │       ├── intent/           L1 规则 / L2 向量 / L3 大模型与意图目录
│   │       ├── prompts/          9 份系统提示词
│   │       ├── skills/           tuniu-cli / flight-manager / flyai / rolling-go-hotel
│   │       └── infrastructure/   网关客户端、途牛、签证、记忆、目的地资讯
│   ├── api-server/               会话 / Run / 差旅单 / 审批 / 偏好 / 审计 / SSE 转发
│   └── tool-gateway/             模型与工具出口、白名单、熔断、流式透传
├── packages/
│   ├── contracts/                OpenAPI、SSE 事件与内部命令 schema
│   └── sensitive-masker/         日志与输出脱敏库
├── migrations/api-server/        Alembic 迁移
├── docker/                       Compose 编排、镜像、出网代理配置
├── docs/                         需求、技术选型、目录结构等设计文档
└── scripts/                      源文件头检查、账号管理、意图直查
```

## 七、快速开始

**前置条件**：Docker Desktop（必需）；本地跑测试需要 Python 3.12 与 uv；前端本机开发需要 Node 20+ 与 pnpm 10。

```powershell
git clone https://github.com/jiang2026-y/travel-agent.git
cd travel-agent

# 1) 生成必需的 4 个密钥文件（命令见 .secrets/README.md，内容不要提交或截图）
#    api_agent_internal_token / agent_gateway_internal_token
#    dashscope_api_key / data_encryption_key
#    可选能力（资讯、签证、长期记忆）不配置也能启动，但 compose 要求文件存在，可先建空文件占位

# 2) 启动全部服务并执行数据库迁移
docker compose -f docker/compose.dev.yml up -d --build

# 3) 打开工作台
start http://localhost:5173
```

本地账号通过 `scripts/manage_users.py` 交互式创建；开发模式可在 `apps/web/.env` 配置 `VITE_DEV_ADMIN_ACCOUNT` / `VITE_DEV_ADMIN_PASSWORD` 实现自动登录（仅本地使用）。

常用运维命令：

```powershell
docker compose -f docker/compose.dev.yml ps                       # 查看容器与健康状态
docker compose -f docker/compose.dev.yml up -d --build agent-server  # 只重建某个服务
docker compose -f docker/compose.dev.yml logs -f api-server       # 跟踪日志
docker compose -f docker/compose.dev.yml down                      # 停止（数据卷保留）
```

## 八、配置与密钥

所有密钥只保存为 Docker Secret 文件，仓库中不保存任何真实值。

| 文件 | 是否必需 | 用途 | 未配置时的行为 |
| --- | --- | --- | --- |
| `api_agent_internal_token` | 必需 | API Server ↔ Agent Server 内部调用 | 服务启动失败 |
| `agent_gateway_internal_token` | 必需 | Agent Server ↔ Tool Gateway 内部调用 | 服务启动失败 |
| `dashscope_api_key` | 必需 | 模型调用、知识库检索、长期记忆鉴权 | 模型调用失败 |
| `data_encryption_key` | 必需 | 敏感字段与消息正文 AES-256-GCM 加密 | 持久化不可用 |
| `newsdata_api_key` | 可选 | 目的地资讯 | 资讯工具返回"未启用" |
| `orizn_visa_api_key` | 可选 | 完整签证能力 | 仅保留免 Key 的两个签证工具 |
| `bailian_memory_library_id` / `bailian_profile_schema` | 可选 | 长期记忆与偏好画像 | 长期记忆降级为不可用 |
| `weather_mcp_endpoint` | 可选 | 超出免费预报范围的天气 | 超范围返回 `beyond_range` |

各类第三方能力均为"未配置即优雅降级"，不会因为缺少密钥而中断主流程。生成密钥的具体命令、记忆库接口与画像 schema 说明见 [.secrets/README.md](./.secrets/README.md)。

## 九、质量门禁与数据

```powershell
uv run python -m pytest services packages -q      # 后端测试
uv run mypy                                        # 严格类型检查
uv run ruff check .                                # 静态检查
uv run python scripts/check_source_headers.py      # 源文件中文职责注释检查
pnpm --filter @travel-agent/web run typecheck      # 前端类型检查
```

| 指标 | 数值 |
| --- | --- |
| 后端源码 | 130 个文件 / 20,774 行 |
| 测试 | 75 个用例文件 / 7,980 行；**300 个用例通过**（3 个可选的真实 PostgreSQL 集成测试按需开启） |
| 类型检查 | mypy strict 131 个源文件 **0 错误** |
| 静态检查 | ruff 全部通过；源文件头检查通过；前端 `tsc` 通过 |
| 契约 | OpenAPI 24 个路径 / 25 个操作，SSE 事件 32 类，内部命令 schema |
| 数据 | PostgreSQL 11 张业务表 + 4 个 Alembic 迁移 |
| 本地编排 | 8 个 Compose 服务 / 4 个网络，全部带健康检查 |

## 十、已知限制与后续计划

- **知识库检索**需要百炼 RAM 子账号具备 `sfm:Retrieve` 权限，未授权时返回稳定错误码 `bailian_permission_denied`；出网域名已放行，属账号权限问题。
- **签证**未升级付费计划，`compare_destinations` 与 `get_recent_changes` 长期保持降级状态，仅部分工具可用。
- **天气**使用免费 `wttr.in`，超出预报范围的日期返回 `beyond_range`。
- **长期记忆抽取是异步的**，写入后约 2-5 秒才可见，连续"写后立刻读"可能取不到最新偏好。
- **未实现**：行程规划子智能体、行程审核子智能体、报销子智能体、SaToken 鉴权。
- **生产化缺口**：CI 流水线、混沌与压测、可观测性（OpenTelemetry）接入、多副本部署下的限流与配额治理。
- 技能正文折叠、大负载卸载（超阈值内容转存）仍在待办清单中。

## 十一、文档索引

| 文档 | 内容 |
| --- | --- |
| [.secrets/README.md](./.secrets/README.md) | 密钥生成、可选能力开关、百炼记忆库接口与画像 schema |
| [docker/README.md](./docker/README.md) | 本地编排、出网白名单与安全约束 |
| [docs/tuniu-provider.md](./docs/tuniu-provider.md) | 途牛 Provider 接入与出网要求 |
| [docs/bailian-knowledge.md](./docs/bailian-knowledge.md) | 百炼知识库检索接入说明 |
| [docs/企业级智能旅行助手需求文档.md](./docs/企业级智能旅行助手需求文档.md) | 需求与验收口径 |
| [docs/企业级智能旅行助手技术选型.md](./docs/企业级智能旅行助手技术选型.md) | 技术选型与取舍 |
| [packages/contracts/README.md](./packages/contracts/README.md) | 契约总览（OpenAPI / 事件 / 命令） |

## 许可

本仓库暂未指定开源许可证；如需对外发布请先补充 LICENSE 并确认其中不包含任何第三方受限资源。
