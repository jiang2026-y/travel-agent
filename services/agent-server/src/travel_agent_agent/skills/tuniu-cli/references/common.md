# 通用参考：服务发现、响应处理与退出码

> 本文档为 tuniu-cli 技能的**服务发现规则、响应格式解析与退出码处理**通用参考。当遇到未知服务、需要解析响应、或处理错误退出码时按需加载。

---

## 服务发现

当遇到以下情况时，**必须**先执行 `tuniu discovery refresh && tuniu discovery list`：

1. 用户需求不在已知服务列表中（如签证、租车等）
2. `tuniu list` 返回的服务不包含用户需要的功能
3. 工具调用返回"工具不存在"错误（退出码 102）
4. 首次使用 tuniu-cli 时（确保获取最新服务列表）

```bash
tuniu discovery refresh && tuniu discovery list
```

**服务发现默认开启**。如不确定，可先执行 `tuniu discovery status` 确认；若返回 `启用: 否`，手动开启：

```bash
export TUNIU_DISCOVERY_ENABLED=true
```

| 命令 | 用途 |
|------|------|
| `tuniu discovery status` | 查看启用状态、缓存状态、服务数量 |
| `tuniu discovery list` | 获取当前可用服务列表（失败时回退静态配置/缓存） |
| `tuniu discovery refresh` | 强制刷新缓存，获取最新服务列表 |

> 工具调用返回退出码 102 时，先执行 `tuniu discovery refresh && tuniu schema --output json`，再重试调用。

### 最佳实践

1. **初始化时**：执行 `tuniu discovery status` 确认服务发现状态（默认开启）
2. **遇到新需求时**：先执行 `tuniu discovery refresh` 刷新缓存，再 `tuniu discovery list` 查看最新服务
3. **获取新服务能力**：执行 `tuniu schema --output json` 获取最新工具定义
4. **降级处理**：如果 discovery 服务不可用，会自动回退到静态配置

---

## 响应处理

### 成功响应

stdout 输出 JSON 格式：

```json
{ "success": true, "result": {...}, "metadata": {...} }
```

- 通常 `tuniu call` 的 stdout 为统一 JSON 包装，业务结果在 `result` 内。
- 对于多数查询/下单工具，业务字段可按 JSON 对象读取。

### 下单成功响应（机票 saveOrder / 酒店 tuniuHotelCreateOrder / 火车票 bookTrain）

下单类工具成功后，`result` 内除订单号外通常还包含**支付链接**，用户需据此完成付款：

| 字段 | 含义 |
|------|------|
| `orderId` / `orderNo` / `mainOrderNo` | 外部订单号 |
| `payUrl` / `paymentUrl` / `cashierUrl` / `orderDetailH5Url` / `orderDetailUrl` | 支付/收银台链接（字段名视服务而定，取其中存在者） |
| `totalAmount` / `price` | 应付金额 |

> **下单成功后必须把支付链接返回给用户**，不能只回复订单号。请在回复中以 Markdown 链接形式输出，例如：
> `订单已创建（订单号：**123456**），请点击 [立即支付](https://...) 完成付款。`
> 若响应中确实没有任何支付链接字段，才只回复订单号并提示用户前往途牛订单中心支付。

### 错误响应

```json
{ "success": false, "error": { "type": "ToolNotFoundError", "message": "工具不存在", "code": 102 } }
```

---

## 退出码含义

| 退出码 | 含义 | 处理建议 |
|--------|------|----------|
| 0 | 成功 | 解析 stdout JSON |
| 101 | 连接失败 | 重试或检查网络 |
| 102 | 工具不存在 | 优先读取 `available_tools` 改用真实工具名；否则运行 `tuniu list <server> -o json` 校验 |
| 103 | 参数错误 | 运行 `tuniu help <server> <tool>` |
| 104 | 认证失败 | 调用 `check_tuniu_api_key` 确认配置；若未配置，引导用户获取并调用 `save_tuniu_api_key` 保存 |
| 105 | 超时 | 使用 `-t 60` 增加超时 |
| 108 | 未配置 API Key | 引导用户前往途牛开放平台获取 Key，然后调用 `save_tuniu_api_key` 保存 |
| 199 | 未知错误 | 使用 `-d` 调试模式 |

---

## 102 错误处理流程

若错误 JSON 含 `error.details.available_tools`，优先从中选真实工具名重试；否则执行 `tuniu list <server> -o json` 获取工具名，再用 `tuniu help`/`tuniu schema` 确认参数。禁止继续用错误工具名重试。
