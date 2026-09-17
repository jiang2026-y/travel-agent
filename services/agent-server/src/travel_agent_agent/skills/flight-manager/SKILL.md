---
name: flight-manager
description: 智程机票出行 MCP 工具调用指南 — 教 Agent 通过 curl + JSON-RPC 调用航班搜索、预订、退改签、发票等 14 个工具。当用户提到机票、航班、退票、改签、发票、订票时使用此技能。
---

# JournAI 机票出行 MCP 工具调用指南

通过 curl + JSON-RPC 调用 JournAI MCP Server 的机票工具。

---

## 调用方式

端点：`POST https://fly.huoli.com/mcp/flight_server`
认证：`Authorization: Bearer ${FLIGHT_API_KEY}`（运行时自动注入，禁止硬编码真实 Key）

**重要：执行任何航班操作前，必须先调用 `check_flight_api_key` 检查用户是否已配置 API Key。**
若未配置，引导用户前往 https://h5.133.cn/webapp/pages/mcpApiKey 获取 Key，用户提供后调用 `save_flight_api_key` 保存。

```bash
curl -s -X POST https://fly.huoli.com/mcp/flight_server \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${FLIGHT_API_KEY}" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"<工具名>","arguments":{...}}}'
```

响应：检查 `result.structuredContent.code` 是否为 0，业务错误通过 `msg` 字段返回。

## 工具入参
详见 [inputSchema.json](inputSchema.json)


## 参数编码

	编码方式：UTF-8
	
---

## 铁律（违反即出错）

### 预订前必须查询并确认乘客信息

调用 `uni_order_create` 之前，**必须先调用 `query_user_contact_info` 查询用户档案中的乘机人信息**（中文姓名、证件类型、证件号码、手机号、性别）。

- 若返回 `flightComplete=true`：直接使用档案中的信息填充 `passengers[]` 和联系人字段，无需再追问用户。
- 若返回 `flightComplete=false`：根据 `message` 中的提示，向用户追问缺少的字段（中文姓名、证件类型、证件号码、手机号、性别）。
- 用户提供信息后，**必须调用 `update_user_contact_info` 保存到档案**，下次预订可自动复用。

联系人字段同样从档案中获取：`phone` → `contactPhone`，`chineseName` → `contactName`。

### 写操作必须确认

以下工具会修改数据，调用前**必须用向用户确认**：
`uni_order_create` `uni_order_cancel` `refund_submit` `reroute_submit` `submit_invoice` `manage_invoice`

### 严格按步骤，不可跳步

**退票：** `uni_order_query` → `refund_query_fee` → 用户确认 → `refund_submit`
**改签：** `uni_order_query` → `reroute_query_flight_filter` → `reroute_confirm` → 用户确认 → `reroute_submit`

### 禁止编造参数

以下参数必须从上游接口返回值获取：`priceId` `flightInfoId` `orderSource` `ticketId` `tripId` `voucherOrderInfo` `postId`

### 国内/国际参数不同

`refund_submit` 和 `reroute_submit` 根据订单号前缀选参数：
- `D` 开头（国内）→ 用 `ticketIds` + 航班信息
- `I` 开头（国际）→ 用 `tripInfoList` + `contacts`

### 多选项必须让用户选

需要用户从多个选项中选择时（选航班、选退哪张票等），**必须询问用户得到明确回答**。

### 时间段偏好 → 排序参数（关键）

用户搜索航班时只要带**时间段偏好**关键词，`search_flights` 就**必须**显式传 `sortBy` + `sortOrder`，**禁止用默认排序或价格排序替代**：

| 用户表达 | sortBy | sortOrder |
|---|---|---|
| 下午/傍晚/晚上/最晚/晚班机 | `departureTime` | `desc` |
| 上午/早上/清晨/最早/早班机 | `departureTime` | `asc` |
| 最快到达/到达最早 | `arrivalTime` | `asc` |
| 到达最晚 | `arrivalTime` | `desc` |
| 最便宜/最低价 | `price` | `asc` |
| 耗时最短/飞行最快 | `duration` | `asc` |

未明确表达时间段偏好时，不传 `sortBy`/`sortOrder`，由服务端按起飞时间正序返回。

### 搜索结果处理策略（关键）

搜索航班后，根据返回结果数量采取不同策略：

**结果太多（totalCount > 20）：**
询问用户是否需要缩小范围，提供以下可选筛选条件供用户选择：
- `maxPrice` / `maxTotalPrice` — 最高价格限制
- `onlyDirect` — 只看直飞
- `excludeShare` — 排除共享航班
- `preferredAirlines` — 只看指定航司
- `excludeAirlines` — 排除指定航司
- `maxTransferCount` — 最大中转次数
- `cabinPreference` — 舱位偏好（Y经济/S超级经济/C公务/F头等）

**结果为空或未找到目标航班：**
1. 先检查是否需要翻页 — 调整 `page` 参数查看后续页
2. 如果已无更多页，增大 `pageSize`（默认10，最大可设50）重新搜索
3. 如果仍无结果，建议用户：
   - 调整日期（前后一天）
   - 放宽筛选条件（如取消航司限制）
   - 选择临近机场（如北京可选 PEK首都/PKX大兴）

**结果适中（1-20条）：**
直接展示结果，无需额外操作。

---

## 工具决策树

```
前置检查 → check_flight_api_key → [未配置则引导用户获取并调 save_flight_api_key]
搜索航班 → search_flights → get_flight_offers → query_user_contact_info → [确认] → uni_order_create
查订单 → uni_order_query
取消订单 → uni_order_cancel（仅限未支付）
退票 → refund_query_fee → [确认] → refund_submit
改签 → reroute_query_flight_filter → [选航班] → reroute_confirm → [确认] → reroute_submit
发票 → query_invoiceable → submit_invoice / query_invoices / manage_invoice
```

---

## 工具详解

### search_flights — 搜索航班

搜索指定日期航班。出发地/目的地支持城市码或机场码。

**必填：** `date` + (`departureCityCode` 或 `departureAirportCode`) + (`arrivalCityCode` 或 `arrivalAirportCode`)
**可选：** `adultNum` `cabinPreference`(Y/S/C/F) `sortBy`(price/totalPrice/departureTime/arrivalTime/duration) `sortOrder`(asc/desc) `page` `pageSize` `maxPrice` `maxTotalPrice` `maxTransferCount` `onlyDirect` `excludeShare` `preferredAirlines` `excludeAirlines`

**示例：用户说「帮我查 5 月 15 日北京到上海下午的航班」**

```json
{
  "departureCityCode": "BJS",
  "arrivalCityCode": "SHA",
  "date": "2026-05-15",
  "adultNum": "1",
  "sortBy": "departureTime",
  "sortOrder": "desc",
  "pageSize": 10
}
```

---

### get_flight_offers — 获取航班报价

查询航班行程的报价详情、舱位分组及可选附加产品。返回的 `priceId`、`flightInfoId`、`orderSource` 是预订必需参数。

**必填：** `flightInfoId`(从 search_flights 获取) `traceId`(调用方生成的唯一标识)
**可选：** `priceId`(指定时获取舱位详情) `from` `phoneId` `adultNum`(默认1) `childNum`(默认0) `cabinPreference`(Y/S/C/F)

---

### uni_order_create — 机票预订 [需确认]

预订国内/国际机票。**调用前必须先调 `query_user_contact_info` 获取乘客信息**，缺什么问用户什么，用户提供后调 `update_user_contact_info` 保存。

**必填：** `priceId` `flightInfoId` `orderSource`(domestic/international) `contactName` `contactPhoneCode` `contactPhone` `passengers[]` `totalPrice`
**乘客必填：** `name` `idType`(0-9) `idCard` `phoneCode` `phone` `type`(ADT) `gender`(M/F)
**国际额外必填：** `birthday` `nationality` `issueNa` `validDate`（且 idType 不能为 0）

**字段映射（从 query_user_contact_info 获取）：**
- `contactName` ← `chineseName`
- `contactPhone` ← `phone`
- `passengers[].name` ← `chineseName`（中文姓名，与证件上一致）
- `passengers[].idType` ← `idType`（证件类型代码：0-身份证 1-护照 2-其他 3-回乡证 4-军官证 5-警官证 6-港澳通行证 7-台胞证 8-台湾通行证 9-外国人永久居留身份证）
- `passengers[].idCard` ← `idNumber`（证件号码）
- `passengers[].phone` ← `phone`
- `passengers[].gender` ← `gender`

---

### uni_order_query — 订单查询

不传 `orderId` 返回列表，传入返回详情（含 ticketId，退改签必需）。
**可选：** `orderSource`(domestic/international) `orderId`

---

### uni_order_cancel — 取消订单 [需确认]

取消未支付订单。已支付请走退票流程。**必填：** `orderId`

---

### refund_query_fee — 退票费计算

查询退票手续费和可退金额。
**必填：** `orderId` `ticketIds`(数组，从 uni_order_query 获取) `refundType`(0=自愿, 1=非自愿)

---

### refund_submit — 退票申请 [需确认]

**必填：** `orderId` `refundType`(0=自愿, 1=非自愿)
**国内必填：** `ticketIds`
**国内非自愿额外：** `refundReasonType`(0-5) `url`(附件)
**国际必填：** `tripInfoList`(segmentIds+passengerIds) `contacts`(phone)

---

### reroute_query_flight_filter — 改期航班列表

**必填：** `orderId` `ticketIds`(数组) `date` `rerouteType`(0=自愿, 1=非自愿)
**可选：** `filterType`(0=不筛选, 1=按时间最近, 2=按价格差最少, 3=同时筛选)

---

### reroute_confirm — 改期费计算

锁定改签航班并获取费用明细。`tripId` `priceId` 从 `reroute_query_flight_filter` 获取。
**必填：** `orderId` `ticketIds` `tripId` `priceId` `date` `rerouteType`

---

### reroute_submit — 改期申请 [需确认]

**必填：** `orderId` `rerouteType`(0=自愿, 1=非自愿)
**国内必填：** `ticketIds` `date` `flightNo` `departureAirportCode` `arrivalAirportCode`
**国际必填：** `tripInfoList`(segmentIds+passengerIds) `contacts`(phone) `expectTravelList`(tripIndex+date)

---

### query_invoiceable — 可报销列表查询

不传查全部，传 `orderId` 查指定订单。
**可选：** `orderId` `pageSize`

---

### submit_invoice — 报销凭证提交 [需确认]

**必填：** `orderId` `voucherOrderInfo`(从 query_invoiceable 获取) `email` `title` `invoiceTitleType`(0=个人, 1=企业, 2=机关事业单位军队)
**企业时必填：** `identityId`(纳税人识别号)

---

### query_invoices — 已提交凭证查询

`postId` 查详情，`orderId` 查订单历史，都空查全部。
**可选：** `postId` `orderId` `pageNum` `pageSize`

---

### manage_invoice — 报销凭证管理 [需确认]

操作：cancel / update / resend。`postId` 从 `query_invoices` 获取。
**必填：** `action` `postId` `orderId`
**resend 必填：** `email`
**update 可选：** `email` `title` `invoiceTitleType` `identityId`（至少填一个）

> 操作前先调 `query_invoices` 确认可操作状态（canCancel/canUpdate/canResend）。

---

## 数据字典

详见 [reference.md](reference.md)（证件类型、舱位代码、退票原因、改签筛选、城市三字码等）。
