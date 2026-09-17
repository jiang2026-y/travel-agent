# 途牛 Provider 配置

当前实现默认使用 `real_readonly`，不会执行真实预订或取消。途牛调用由 Agent 服务端通过 `tuniu` CLI 发起，浏览器不会获得 API Key。

## 出网要求

`tuniu` CLI 需要访问 `https://openapi.tuniu.cn/hybrid/mcp/<server>`，因此 **Agent Server 必须具备公网出口**：

- 开发环境由 `docker/compose.dev.yml` 把 `agent-server` 接入 `egress-network` 直连公网。
- 若容器只挂在 `internal: true` 的网络上，容器内 DNS 会直接解析失败，查询、下单与取消都会以 `tuniu_provider_call_failed` 降级。
- Node 的内置 fetch 不读取 `HTTP_PROXY` / `HTTPS_PROXY`，因此改用代理环境变量无法替代出网通道。

由于该容器同时可访问公网并挂着受控 shell 工具，`execute_shell_command` 已拒绝包含 `/run/secrets` 的命令，避免直接读取挂载的密钥文件。

启用前请在 Docker Compose 的 Agent 服务中挂载名为 `tuniu_api_key` 的 Secret 到 `/run/secrets/tuniu_api_key`，并同时设置：

```yaml
TRAVEL_AGENT_EXTERNAL_MODE: real_tuniu
TRAVEL_AGENT_TUNIU_APPROVED: "true"
TUNIU_API_KEY_FILE: /run/secrets/tuniu_api_key
```

未配置 CLI、密钥文件或审批开关时，能力清单会显示途牛写操作未启用。机票、火车票和酒店下单均需要用户二次确认；服务端只创建待支付订单并返回 HTTPS 支付链接，不代替用户付款。Provider 失败时不会写入订单成功状态。
