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

边缘网络仅承载 Web 与 API Server 的宿主机端口发布；API、Agent、PostgreSQL 与 Redis 使用内部后端网络；Agent Server 只能通过 `tool-gateway-network` 访问 Tool Gateway，不能直接访问公网。Egress Proxy 只允许访问已确认的北京地域百炼工作空间域名的 HTTPS CONNECT；Tool Gateway 仅转发 `text-embedding-v4`（1024 维）和 `glm-5.1` 的只读推理请求。意图种子以每批最多 10 条的 embedding 请求初始化，68 条种子会发出 7 个请求；旅行供应商、MCP、Skill 写操作仍保持拒绝。

## 停止

```powershell
docker compose -f docker/compose.dev.yml down
```

如需删除本地开发数据卷，必须先确认目标环境和数据可删除性；本阶段不执行删除卷操作。
