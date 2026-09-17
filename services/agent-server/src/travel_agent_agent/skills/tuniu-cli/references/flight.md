# 机票服务 (flight)

> 本文档为 tuniu-cli 技能的**机票服务详细参数、下单流程与示例**参考。当需要查询/预订机票、确认具体参数或翻页细节时按需加载。

**触发词**：航班、机票、飞机、某地到某地航班、查机票、机票价格

---

## 1. 航班搜索 (searchLowestPriceFlight)

**支持 6 种查询模式**：
- **默认低价查询**：不传 searchType
- **TIME 时间范围查询**：searchType="TIME"，按出发/到达时间筛选
- **PRICE 价格区间查询**：searchType="PRICE"，按价格区间筛选
- **NEAR_GO 周边出发**：searchType="NEAR_GO"，查询出发地周边机场
- **NEAR_BACK 周边到达**：searchType="NEAR_BACK"，查询目的地周边机场
- **TRANSFER 中转查询**：searchType="TRANSFER"，查询中转航班

**必填参数**：`departureCityName`、`arrivalCityName`、`departureDate`（YYYY-MM-DD）
**翻页**：传相同城市日期参数 + `pageNum`（2=第二页，3=第三页…）

```bash
# 默认低价查询
tuniu call flight searchLowestPriceFlight -a '{"departureCityName":"北京","arrivalCityName":"上海","departureDate":"2026-03-15"}'

# TIME 模式：早班机
tuniu call flight searchLowestPriceFlight -a '{"departureCityName":"北京","arrivalCityName":"上海","departureDate":"2026-03-15","searchType":"TIME","departureTime":"06:00-10:00"}'

# 翻页查询
tuniu call flight searchLowestPriceFlight -a '{"departureCityName":"北京","arrivalCityName":"上海","departureDate":"2026-03-15","pageNum":2}'
```

## 2. 舱位详情查询 (multiCabinDetails)

**必填参数**：`departureCityName`、`arrivalCityName`、`departureDate`（YYYY-MM-DD）、`flightNo`
**返回**：`cabinPriceId`（下单必需）

```bash
tuniu call flight multiCabinDetails -a '{"departureCityName":"北京","arrivalCityName":"上海","departureDate":"2026-03-15","flightNo":"MU5101"}'
```

## 3. 创建订单 (saveOrder)

**前置条件**：必须先调用 `searchLowestPriceFlight` 和 `multiCabinDetails` 获取 `cabinPriceId`。
**个人信息来源**：调用 `query_user_contact_info`，检查 `flightComplete=true`。
- 若完整，直接填充：`tourists[].name` ← `chineseName`，`tourists[].idType` ← `idTypeLabel`，`tourists[].idNumber` ← `idNumber`，`tourists[].mobile` ← `phone`；`contactTourist.name` ← `chineseName`，`contactTourist.mobile` ← `phone`。
- 若缺失字段，根据 `message` 追问用户，得到后调 `update_user_contact_info` 保存。

**必填参数**：`departureCityName`、`arrivalCityName`、`departureDate`、`flightNo`、`cabinPriceId`、`tourists`、`contactTourist`

```bash
tuniu call flight saveOrder -a '{"departureCityName":"北京","arrivalCityName":"上海","departureDate":"2026-03-15","flightNo":"MU5101","cabinPriceId":"xxx","tourists":[{"name":"张三","idType":"身份证","idNumber":"310101199001011234","mobile":"13800138000"}],"contactTourist":{"name":"张三","mobile":"13800138000"}}'
```

## 4. 取消订单 (cancelOrder)

```bash
tuniu call flight cancelOrder -a '{"orderId":"订单号"}'
```

---

## 机票偏好应用

> 当用户长期记忆中存在差旅偏好时，Agent 应在搜索/筛选/推荐环节**自动应用**这些偏好，无需用户每次重复说明。

| 偏好项 | 偏好值 | 对 API 的影响 |
|--------|--------|---------------|
| 航班时间 `flight_time` | 早班(6:00-9:00) | `searchLowestPriceFlight` 增加 `"searchType":"TIME", "departureTime":"06:00-09:00"` |
| | 上午(9:00-12:00) | 增加 `"searchType":"TIME", "departureTime":"09:00-12:00"` |
| | 下午(12:00-18:00) | 增加 `"searchType":"TIME", "departureTime":"12:00-18:00"` |
| | 晚班(18:00-21:00) | 增加 `"searchType":"TIME", "departureTime":"18:00-21:00"` |
| | 红眼航班也可以 | 不限制时间，使用默认低价查询 |
| 中转偏好 `flight_direct` | 只选直飞 | **不使用** `searchType:"TRANSFER"`，仅查直飞结果 |
| | 可接受一次中转 | 直飞结果不理想时，可追加 `searchType:"TRANSFER"` 查询中转方案 |
| | 价格优先不限中转 | 同时查询直飞和 `searchType:"TRANSFER"`，综合比价推荐 |
| 偏好航司 `flight_airline` | 如 国航(CA)、东航(MU) | 搜索结果中**优先展示/推荐**该航司的航班；结果排序时偏好航司靠前 |
| 舱位偏好 `flight_cabin` | 经济舱 / 公务舱 / 头等舱 | 调用 `multiCabinDetails` 后，从返回的舱位列表中**优先推荐**匹配舱位 |
| 座位位置 `flight_seat` | 靠窗 / 靠过道 / 前排 | 选座环节的推荐依据（API 不支持座位筛选，作为结果推荐参考） |
| 费用敏感度 `reimburse_priority` | 价格优先 | 使用默认低价模式 |
| | 体验优先 | 优先直飞/好时段 |

**示例**：用户偏好「上午出发」+「只选直飞」+「偏好东航」

```bash
# 应用时间偏好：使用 TIME 模式限定 09:00-12:00
tuniu call flight searchLowestPriceFlight -a '{"departureCityName":"北京","arrivalCityName":"上海","departureDate":"2026-03-15","searchType":"TIME","departureTime":"09:00-12:00"}'
# 从结果中优先推荐东航(MU)的航班
```

---

## 偏好应用规则

1. **显式指定优先**：用户在当前对话中明确提出的需求覆盖长期记忆偏好。
2. **无偏好不限制**：偏好值为"无偏好"时，不添加额外筛选参数。
3. **渐进应用**：先用偏好参数查询；若结果为空或极少，放宽条件重新查询并告知用户。
4. **透明告知**：应用偏好时简要告知用户（如"根据您的偏好，已筛选上午出发的航班"）。
