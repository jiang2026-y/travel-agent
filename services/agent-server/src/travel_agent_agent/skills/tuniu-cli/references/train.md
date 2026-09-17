# 火车票服务 (train)

> 本文档为 tuniu-cli 技能的**火车票服务详细参数、下单流程与示例**参考。当需要查询/预订火车票、确认具体参数或翻页细节时按需加载。

**触发词**：火车票、火车、车次、某站到某站火车、高铁、动车

---

## 1. 查询车次列表 (searchLowestPriceTrain)

**必填参数**：`departureCityName`、`arrivalCityName`、`departureDate`（yyyy-MM-dd）
**可选参数**：`departureTime`、`arrivalTime`（时间范围，如"08:00-12:00"）、`searchType`（查询模式，默认值 `5`）

**searchType 取值说明**：
- `1`：按出发时间升序
- `2`：按出发时间降序
- `3`：按行程耗时升序
- `4`：按行程耗时降序
- `5`：按票价升序（默认）
- `6`：按票价降序

**翻页**：传首次查询返回的 `queryId` 和 `pageNum`

```bash
# 首次查询
tuniu call train searchLowestPriceTrain -a '{"departureCityName":"南京","arrivalCityName":"上海","departureDate":"2026-03-20","searchType":"5"}'

# 翻页
tuniu call train searchLowestPriceTrain -a '{"queryId":"xxx","pageNum":2}'
```

## 2. 查询车次详情 (queryTrainDetail)

**必填参数**：`departureStationName`、`arrivalStationName`、`departureDate`、`trainNum`
**返回**：`resId`、`price`、`departsDate`（下单必需）

```bash
tuniu call train queryTrainDetail -a '{"departureStationName":"南京南","arrivalStationName":"上海虹桥","departureDate":"2026-03-20","trainNum":"G203"}'
```

## 3. 预订下单 (bookTrain)

**前置条件**：必须先调用 `searchLowestPriceTrain` 和 `queryTrainDetail`。
**个人信息来源**：调用 `query_user_contact_info`，检查 `flightComplete=true`。
- 若完整，直接填充：`adultTourists[].name` ← `chineseName`，`adultTourists[].psptId` ← `idNumber`，`adultTourists[].tel` ← `phone`；`contact.tel` ← `phone`。`psptType` 按途牛 schema 由 `idType` / `idTypeLabel` 转换得到。
- 若缺失字段，根据 `message` 追问用户，得到后调 `update_user_contact_info` 保存。

**必填参数**：`resources`、`adultTourists`、`contact`、`acceptStandingTicket`

> ⚠️ 证件类型 `psptType` 为**数字编码**：`1=身份证`、`2=护照`。请以 `idTypeLabel`（中文名）为基准映射（身份证→`1`，护照→`2`）；**切勿直接透传用户档案的 `idType`**（档案 `0=身份证` 与本接口编码不同，透传 `0` 会报"成人出游人证件类型不能为空"）。与机票用中文字符串（`"身份证"`）不同。

```bash
tuniu call train bookTrain -a '{"acceptStandingTicket":false,"adultTourists":[{"name":"张三","psptId":"310101199001011234","psptType":1,"isStuDisabledArmyPolice":0,"tel":"13800138000"}],"contact":{"tel":"13800138000"},"resources":[{"resourceId":2121337089,"adultPrice":141.0,"departsDate":"2026-03-20"}]}'
```

## 4. 取消订单 (cancelOrder)

```bash
tuniu call train cancelOrder -a '{"orderId":"订单号"}'
```

---

## 火车票偏好应用

> 当用户长期记忆中存在差旅偏好时，Agent 应在搜索/筛选/推荐环节**自动应用**这些偏好，无需用户每次重复说明。

| 偏好项 | 偏好值 | 对 API 的影响 |
|--------|--------|---------------|
| 出发时段 `train_time` | 早班(6:00-9:00) | `searchLowestPriceTrain` 增加 `"departureTime":"06:00-09:00"` |
| | 上午(9:00-12:00) | 增加 `"departureTime":"09:00-12:00"` |
| | 下午(12:00-18:00) | 增加 `"departureTime":"12:00-18:00"` |
| | 晚班(18:00-21:00) | 增加 `"departureTime":"18:00-21:00"` |
| 座位等级 `train_seat` | 二等座 / 一等座 / 商务座 | `queryTrainDetail` 返回多个座位等级的价格，**优先推荐**用户偏好的等级；下单时 `resources[].adultPrice` 填对应等级价格 |
| 座位位置 `train_position` | 靠窗 / 靠过道 | 作为推荐参考（火车票 API 不支持选座筛选） |
| 费用敏感度 `reimburse_priority` | 价格优先 | `searchType:"5"`（票价升序） |
| | 体验优先 | `searchType:"3"`（耗时升序选快车） |

**示例**：用户偏好「上午出发」+「一等座」

```bash
# 应用时间偏好：限定 09:00-12:00 出发
tuniu call train searchLowestPriceTrain -a '{"departureCityName":"南京","arrivalCityName":"上海","departureDate":"2026-03-20","departureTime":"09:00-12:00"}'
# 查详情后优先推荐一等座的价格和余票
```

---

## 偏好应用规则

1. **显式指定优先**：用户在当前对话中明确提出的需求覆盖长期记忆偏好。
2. **无偏好不限制**：偏好值为"无偏好"时，不添加额外筛选参数。
3. **渐进应用**：先用偏好参数查询；若结果为空或极少，放宽条件重新查询并告知用户。
4. **透明告知**：应用偏好时简要告知用户（如"根据您的偏好，已筛选上午出发的车次"）。
