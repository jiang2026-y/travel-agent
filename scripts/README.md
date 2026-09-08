# 开发检查脚本

## 源码文件头检查

`check_source_headers.py` 检查 `apps/web/src` 与 `services` 下的 Python、TypeScript、TSX 源文件。每个文件的首个非空行必须是对应语言的注释，并包含中文职责说明。

在仓库根目录执行：

```powershell
uv run python scripts/check_source_headers.py
```

只检查指定目录：

```powershell
uv run python scripts/check_source_headers.py --path services/api-server/src
```

该脚本不修改源码；发现不符合规范的文件时返回非零退出码，供本地开发和后续 CI 使用。

## 企业账号初始化

`manage_users.py` 是唯一允许创建 PostgreSQL 预置账号的管理脚本。它交互式读取密码，以 Argon2id 保存哈希；证件、手机号、姓名拼音等可选档案字段以 AES-256-GCM 加密写入。脚本不接受密码命令行参数，不会打印密码、密文或敏感字段。

在仓库根目录、PostgreSQL 已迁移且 Docker Secret 已创建后执行：

```powershell
$env:DATABASE_URL='postgresql+asyncpg://travel_agent_dev:local_development_only@localhost:5432/travel_agent_dev'
$env:TRAVEL_AGENT_DATA_ENCRYPTION_KEY_FILE="$PWD/.secrets/data_encryption_key"
uv run python scripts/manage_users.py create --username admin --role admin --base-city 北京 --level P8
```

生产账号创建、密码哈希参数、管理员授予和密钥轮换仍须遵循 G2/G7/G8 审核流程。

## 意图识别直查

`check_intent.ps1` 会从 Agent 容器内部调用受保护的 Run 启动命令，绕过浏览器登录、Cookie、CSRF 与 PostgreSQL，用于快速查看当前真实意图识别和 Master 路由结果。它不会打印内部 Token 或百炼 API Key，但会在 Redis 创建一个 7 天后过期的测试检查点。

在仓库根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_intent.ps1 -Message '查询北京到上海的航班'
```

结果中的 `intent_source` 含义如下：`RULE` 为 L1 正则命中，`VECTOR` 为 L2 向量命中，`LLM` 为 L3 或未知意图回退；`routing_action=direct_dispatch` 表示高置信单意图直跳，`clarify` 表示交由 Master Agent 澄清。
