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
