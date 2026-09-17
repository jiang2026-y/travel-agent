# 本地 Docker Secret 说明

本目录仅保存本机开发环境的 Docker Compose Secret，已由 Git 忽略，禁止提交、打印或复制其中的实际内容。

首次启动前，在本目录生成 `api_agent_internal_token` 文件。文件内容须为随机、长度至少 32 个字符的单行 Token；API Server 与 Agent Server 以 Docker Secret 方式只读挂载该文件，环境变量中只保存挂载路径。

可在 PowerShell 中执行以下命令生成一次本机 Token：

```powershell
$rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
$bytes = New-Object byte[] 32
$rng.GetBytes($bytes)
$rng.Dispose()
[System.IO.File]::WriteAllText('.secrets/api_agent_internal_token', ([System.BitConverter]::ToString($bytes) -replace '-', ''))
```

Token 轮换时，先停止相关容器、重新生成文件，再重建 API Server 和 Agent Server。不得通过 `docker compose config`、日志、截图或聊天内容披露 Token。

## Agent 到 Tool Gateway 的内部 Token

Agent Server 调用 Tool Gateway 使用与 API-Agent Token 完全独立的 `agent_gateway_internal_token`。生成方式相同，但必须使用不同随机值：

```powershell
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
[System.IO.File]::WriteAllText('.secrets/agent_gateway_internal_token', ([System.BitConverter]::ToString($bytes) -replace '-', ''))
```

## 百炼 API Key

将**已轮换**的百炼 API Key 单独保存为 `.secrets/dashscope_api_key`，文件只包含该 Key 的一行文本。不要把 Key 放进 `compose.dev.yml`、`.env`、源代码、日志或聊天内容。Tool Gateway 是唯一挂载该 Secret 的服务；Agent Server 仅持有上面的内部 Token。

```powershell
# 请将尖括号替换为新生成的 Key；执行后不要回显或截图该文件内容。
[System.IO.File]::WriteAllText('.secrets/dashscope_api_key', '<新百炼APIKey>')
```

## 数据加密密钥

API Server 使用 `data_encryption_key` Docker Secret 保护用户证件、手机号、姓名拼音和加密消息正文。文件内容必须是 Base64 编码的 32 字节随机值，不能提交、打印或写入环境变量。

```powershell
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
[System.IO.File]::WriteAllText('.secrets/data_encryption_key', [Convert]::ToBase64String($bytes))
```

密钥版本当前固定为 `1`。密钥丢失会导致数据库中的敏感字段不可解密；轮换前必须先设计双版本读取和重加密流程，当前不支持直接覆盖旧密钥。

## 可选能力 Secret（未创建即自动降级）

以下文件全部为**可选**：文件不存在时对应工具返回"未启用"的安全降级结果，不会中断主流程，也不会使用任何模拟数据。创建文件只包含单行文本，并在 `docker/compose.dev.yml` 的 `tool-gateway` 服务 `secrets` 列表中登记同名条目后重建容器。

注意：`docker compose` 要求 Secret 文件存在，因此即使尚未申请到 Key，也需要先创建对应的**空文件**占位（网关按长度判定为未配置并降级），否则容器无法启动。当前已在 `docker/compose.dev.yml` 登记的条目为 `newsdata_api_key`、`orizn_visa_api_key`、`bailian_memory_library_id`、`bailian_profile_schema`。

| 文件 | 用途 | 未配置时的行为 |
| --- | --- | --- |
| `newsdata_api_key` | 目的地资讯查询（newsdata.io） | 资讯查询返回"未启用"提示 |
| `weather_mcp_endpoint` | 超过免费预报范围的更长期天气 MCP 端点（URL 内嵌云市场 AppCode） | 超范围日期只返回 `beyond_range` 提示 |
| `bailian_memory_library_id` | 百炼记忆库 ID | 长期记忆读写返回"未启用"，不写入任何偏好 |
| `bailian_project_id` | 百炼记忆库项目标识（可选） | 写入时省略 `project_id`，召回时省略 `project_ids` |
| `bailian_profile_schema` | 百炼记忆库画像 schema（可选） | 写入时省略 `profile_schema` |
| `orizn_visa_api_key` | Orizn 签证服务访问密钥（可选） | 仅 `quick_visa_check` 与 `get_coverage_stats` 可用，其余签证工具返回 `orizn_visa_key_required` |

## 签证（Orizn）

签证能力经 Tool Gateway 调用 `https://visa.orizn.app/api/v1`（`/visa`、`/visa/check`、`/visa/bulk`、`/visa/changes`、`/visa/stats`），请求头 `x-api-key: <orizn_visa_api_key>` 并附带 `Referer: https://visa.orizn.app/mcp`。

- **免 Key 模式**：`quick_visa_check`（`/visa/check`）与 `get_coverage_stats`（`/visa/stats`）无需密钥即可调用。
- **全量模式**：在 [visa.orizn.app](https://visa.orizn.app) 免费注册后，把密钥（`orizn_visa_` 开头）单行写入 `.secrets/orizn_visa_api_key`，即可解锁 `check_visa_requirement`、`check_transit_visa`、`compare_destinations`、`get_recent_changes`。
- 该文件缺失或为空时，容器仍可正常启动：网关把它视为未配置，只放行免 Key 两个工具。

长期记忆复用 `dashscope_api_key`（Authorization: Bearer）作为访问凭据，**不使用** `bailian_access_key_id` / `bailian_access_key_secret`，因此不需要为 RAM 用户配置 `sfm:*` 记忆库权限。

## 记忆库接口与画像 schema

长期记忆使用百炼记忆库 v2 接口，路径已与 AgentScope Java `BailianMemoryClient` 及官方 `agentscope-runtime`（`DEFAULT_MEMORY_SERVICE_ENDPOINT = https://dashscope.aliyuncs.com/api/v2/apps/memory`）逐项核对：

| 用途 | 方法与路径 |
| --- | --- |
| 写入偏好 | `POST /api/v2/apps/memory/add` |
| 召回偏好 | `POST /api/v2/apps/memory/memory_nodes/search` |
| 列出记忆节点 | `GET /api/v2/apps/memory/memory_nodes?user_id=...` |
| 创建画像 schema | `POST /api/v2/apps/memory/profile_schemas` |
| 列出画像 schema | `GET /api/v2/apps/memory/profile_schemas` |
| 查询用户画像 | `GET /api/v2/apps/memory/profile_schemas/{schema_id}/user_profile?user_id=...` |

写入请求体包含 `memory_library_id`、`user_id`、`messages`（`role`/`content`）与 `meta_data.source`，可选 `project_id`、`profile_schema`；召回请求体额外带 `top_k`（当前 20），可选 `project_ids`。响应中的 `memory_nodes[].content` 会拼接后返回给 Agent。

### 必须配置画像 schema

`profile_schema` 不是内联定义，而是**服务端已注册的 schema ID**：传未注册值会返回 `InvalidParameter: Profile schema not found`。记忆库里默认只有"默认画像"（姓名、出生日期、居住地等个人基础字段），抽不出差旅偏好，因此本项目已创建专用 schema：

```
名称：差旅偏好画像
ID：<在控制台创建后填入，或直接写入 .secrets/bailian_profile_schema>
字段：常飞航司 / 舱位偏好 / 座位偏好 / 酒店品牌偏好 / 房型偏好 / 出发时段偏好 / 火车席别偏好 / 特殊服务需求
```

本机实际使用的 ID 只保存在 `.secrets/bailian_profile_schema`（已被 Git 忽略，不会入库）；如需调整字段，可在控制台编辑该 schema，或用 `POST /api/v2/apps/memory/profile_schemas` 新建后替换该文件内容。

### 当前验证状态（2026-09-16，已跑通）

在 `tool-gateway` 容器内实测（`user_id=deploy_final`）：

```
record   -> available=True, count=3
retrieve -> available=True, count=3
memories -> 用户出差只乘坐国航 / 用户出差喜欢靠窗座位 / 用户出差住宿选择全季酒店
```

结论与注意事项：

1. 必须先有画像 schema 才会抽取；默认画像只覆盖个人基础字段，差旅偏好不会被抽取。
2. `meta_data` 是正确字段名（`metadata` 会被忽略）；鉴权用 `Authorization: Bearer <dashscope_api_key>`，与 `bailian_access_key_id` / `bailian_access_key_secret` 无关（后者的 RAM 用户缺少 `sfm:*` 权限，会返回 403，但不影响本功能）。
3. 抽取是异步的：写入后约 2-5 秒才能在 `memory_nodes` 中查到，因此 Agent 连续"写后立刻读"可能拿不到新鲜记忆。
4. `/api/v2/apps/memory/memory_nodes/search` 在本账号下即使节点已存在也返回空，因此网关的召回改用 `GET /api/v2/apps/memory/memory_nodes?user_id=...&page_size=20&page_num=1` 按用户列举节点；如后续该 search 接口恢复可用，可再切换回语义检索。
5. 记忆按 `user_id` 隔离，`user_id` 由 Agent 侧传入当前登录用户标识。

验证期间写入的探针用户（可在控制台清理）：`probe_doc_example`、`probe_project_user`、`probe_noschema_user`、`probe_schema_json`、`probe_with_project`、`memory_probe_user`、`memory_probe_user2`、`memory_probe_pair`、`memory_async_user`、`memory_e2e_user`、`deploy_probe`、`deploy_e2e`、`deploy_verify`、`deploy_final`。

启用后可用以下命令自检（结果应为 `available: true`）：

```powershell
docker compose -f docker/compose.dev.yml exec tool-gateway python -c "import json,pathlib,urllib.request as u; t=pathlib.Path('/run/secrets/agent_gateway_internal_token').read_text().strip(); r=u.Request('http://127.0.0.1:8002/internal/v1/bailian/memory/retrieve', data=json.dumps({'user_id':'probe_user','query':'座位偏好'}).encode(), headers={'Authorization':'Bearer '+t,'Content-Type':'application/json'}); print(u.urlopen(r).read().decode())"
```
