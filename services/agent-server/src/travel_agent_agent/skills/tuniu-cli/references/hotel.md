# 酒店服务 (hotel)

> 本文档为 tuniu-cli 技能的**酒店服务详细参数、下单流程与示例**参考。当需要查询/预订酒店、确认具体参数时按需加载。

**触发词**：酒店、住宿、民宿、某地酒店、入住、查酒店

---

## 1. 酒店搜索 (tuniuHotelSearch)

**必填参数**：`cityName`
**可选参数**：`checkIn`、`checkOut`（YYYY-MM-DD）、`keyword`、`prices`
**翻页**：传 `queryId`（首次搜索返回）和 `pageNum`

```bash
# 第一页
tuniu call hotel tuniuHotelSearch -a '{"cityName":"北京","checkIn":"2026-03-01","checkOut":"2026-03-03"}'

# 翻页（使用 queryId）
tuniu call hotel tuniuHotelSearch -a '{"queryId":"xxx","pageNum":2}'
```

## 2. 酒店详情 (tuniuHotelDetail)

**必填参数**：`hotelId` 或 `hotelName` 二选一

> ⚠️ 本接口 `hotelId` 必须为**数字 number**（如 `214076776`），与下单接口 `tuniuHotelCreateOrder` 要求的**字符串**不同，勿混用。

```bash
tuniu call hotel tuniuHotelDetail -a '{"hotelId":12345,"checkIn":"2026-03-01","checkOut":"2026-03-03"}'
```

## 3. 创建订单 (tuniuHotelCreateOrder)

**前置条件**：必须先调用 `tuniuHotelDetail` 获取 `preBookParam`。
**个人信息来源**：调用 `query_user_contact_info`，检查 `hotelComplete=true`。
- 若完整，直接填充：`roomGuests[].lastName` ← `lastName`，`roomGuests[].firstName` ← `firstName`；`contactName` ← `chineseName`（或 `lastName` + `firstName`），`contactPhone` ← `phone`。
- 若缺失字段，根据 `message` 追问用户，得到后调 `update_user_contact_info` 保存。

**必填参数**：`hotelId`、`roomId`、`preBookParam`、`checkInDate`、`checkOutDate`、`roomCount`、`roomGuests`、`contactName`、`contactPhone`

> ⚠️ 类型注意：本接口 `hotelId` 必须为**字符串 string**（如 `"214076776"`），与详情接口 `tuniuHotelDetail` 要求的**数字**不同。
> ⚠️ 报价（`preBookParam`）易失效：若距上次查详情间隔较久或报"报价信息未找到或已失效"，应**临下单前重新调 `tuniuHotelDetail`（传相同 checkIn/checkOut）刷新 `preBookParam`** 后再下单；若刷新后价格变动，需重新向用户确认新价。

### roomGuests 格式说明（重要）

`roomGuests` 为 JSON 数组，每个元素代表一间房的入住人信息：

```json
[{"guests":[{"firstName":"三","lastName":"张"}]}]
```

- 外层数组长度 = `roomCount`（几间房）
- 每间房内 `guests` 数组包含该房间的入住人
- `firstName` = 名（如"三"），`lastName` = 姓（如"张"）
- 来源：`query_user_contact_info` 返回的 `firstName` 和 `lastName` 字段

### 完整下单示例

```bash
tuniu call hotel tuniuHotelCreateOrder -a '{"hotelId":"214076776","roomId":"room_001","preBookParam":"eyJ...","checkInDate":"2026-03-01","checkOutDate":"2026-03-03","roomCount":1,"roomGuests":[{"guests":[{"firstName":"三","lastName":"张"}]}],"contactName":"张三","contactPhone":"13800138000"}'
```

---

## 酒店偏好应用

> 当用户长期记忆中存在差旅偏好时，Agent 应在搜索/筛选/推荐环节**自动应用**这些偏好，无需用户每次重复说明。

| 偏好项 | 偏好值 | 对 API 的影响 |
|--------|--------|---------------|
| 星级偏好 `hotel_star` | 经济型 / 舒适型(三星) / 高档型(四星) / 豪华型(五星) | `tuniuHotelSearch` 的 `prices` 参数区间参考：经济型 0-300，舒适型 300-500，高档型 500-1000，豪华型 1000+ |
| 偏好品牌 `hotel_brand` | 如 全季、亚朵、希尔顿 | `tuniuHotelSearch` 的 `keyword` 参数传入品牌名进行筛选 |
| 房型偏好 `hotel_room` | 大床房 / 双床房 | `tuniuHotelDetail` 返回多种房型，**优先推荐**用户偏好的房型 |
| 楼层偏好 `hotel_floor` | 高楼层 / 低楼层 | 作为推荐参考（API 不支持楼层筛选） |
| 位置偏好 `hotel_location` | 靠近地铁等 | `tuniuHotelSearch` 的 `keyword` 可追加位置描述辅助筛选 |
| 设施需求 `hotel_facilities` | 健身房、早餐等 | `tuniuHotelDetail` 返回设施信息，筛选/优先推荐包含所需设施的酒店 |
| 费用敏感度 `reimburse_priority` | 价格优先 | `prices` 设较低区间 |
| | 体验优先 | 可放宽 prices 上限 |

**示例**：用户偏好「高档型(四星)」+「亚朵」

```bash
# 应用品牌偏好作为 keyword
tuniu call hotel tuniuHotelSearch -a '{"cityName":"北京","checkIn":"2026-03-01","checkOut":"2026-03-03","keyword":"亚朵"}'
# 若无品牌偏好但有星级偏好，使用 prices 区间
tuniu call hotel tuniuHotelSearch -a '{"cityName":"北京","checkIn":"2026-03-01","checkOut":"2026-03-03","prices":"500-1000"}'
```

---

## 偏好应用规则

1. **显式指定优先**：用户在当前对话中明确提出的需求覆盖长期记忆偏好。
2. **无偏好不限制**：偏好值为"无偏好"时，不添加额外筛选参数。
3. **渐进应用**：先用偏好参数查询；若结果为空或极少，放宽条件重新查询并告知用户。
4. **透明告知**：应用偏好时简要告知用户（如"根据您的偏好，已筛选高档型酒店"）。
