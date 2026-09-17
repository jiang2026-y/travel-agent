# 百炼知识库检索配置

Tool Gateway 使用百炼旧版 Retrieve 接口为 InfoAgent 提供只读检索。Agent Server 不读取百炼 AccessKey，只接收经过网关裁剪的文本片段和来源摘要。

当前开发配置：

```ini
BAILIAN_WORKSPACE_ID=ws-afyh9lpghkjx1iz8
BAILIAN_INDEX_ID=file_921c201ec9854ef3a3e8c999c5867f15_16035326
```

启用前，在项目 `.secrets` 目录创建两个未提交文件：

- `bailian_access_key_id`：AccessKey ID；
- `bailian_access_key_secret`：AccessKey Secret。

然后在 `docker/compose.dev.yml` 的 `tool-gateway` 服务中挂载这两个 Docker Secret，并将 `BAILIAN_ENABLED` 改为 `true`。密钥只挂载到 Tool Gateway，禁止写入环境变量、源码、日志或浏览器。

同时在 `api-server` 服务增加 `BAILIAN_ENABLED: "true"`，这样前端能力清单才会显示 InfoAgent 已启用；API Server 不需要挂载 AccessKey。

未创建凭据或 `BAILIAN_ENABLED=false` 时，InfoAgent 会安全降级为“知识库暂不可用”，不会使用模拟数据，也不会改变主运行状态。
