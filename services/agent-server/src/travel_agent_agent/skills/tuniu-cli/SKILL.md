---
name: tuniu-cli
description: 途牛旅行统一助手- 通过 tuniu CLI 统一调用机票、酒店、火车票等旅行服务。适用于用户询问航班、酒店、火车票相关需求的场景。只针对国内酒店、机票、火车票的查询和预定。
version: 1.0.4
minCliVersion: 1.0.7
---

# 途牛旅行助手

当用户询问航班、酒店、火车票等旅行服务时，使用此 skill 通过 **tuniu CLI** 调用途牛服务。

## 按需加载的附属文档（重要）

为节省上下文，各服务详细参数已按类型拆分为独立文档。**仅在需要时**用 `load_skill_through_path` 加载对应文档：

| 文档 | 何时加载 |
|------|----------|
| `references/flight.md` | 需要查询/预订**机票**、确认机票参数、机票偏好时 |
| `references/hotel.md` | 需要查询/预订**酒店**、确认酒店参数、酒店偏好时 |
| `references/train.md` | 需要查询/预订**火车票**、确认火车票参数、火车票偏好时 |
| `references/common.md` | 遇到**退出码错误**、需要解析响应格式、或触发**服务发现**时 |
| `references/setup.md` | 首次使用或遇到 CLI 不可用/版本过低时 |

> **环境不可用时的唯一处置**：若 `tuniu` 报 `command not found`（退出码 127）或版本低于 `minCliVersion`，直接执行 `bash scripts/setup.sh` 完成自检与自动安装，按其输出的 `RESULT=` 决定下一步（细则见 `references/setup.md`）。**不要**手工逐条摸索 `which`/`npm root -g`/绝对路径/`npx tuniu-cli` 等命令。

> **按需精准加载**：每次只加载当前步骤所需的那一份文档。例如预订酒店时加载 `hotel.md`，不要加载 `flight.md`。
> 本 SKILL.md 已包含意图速查表、命令格式、下单字段映射与安全规则，日常路由与参数组织通常无需再加载附属文档。

## 配置与 API Key 安全

- **TUNIU_API_KEY** 是途牛开放平台的敏感凭证，每个用户独立保存。**执行任何 `tuniu call` 前，必须先调用 `check_tuniu_api_key` 检查用户是否已配置。**
- 已配置：直接调用 `tuniu`，不要重复索要。
- 未配置：提示用户前往 [途牛开放平台](https://open.tuniu.com/mcp/login) 获取；仅当用户主动提供并希望配置时，才调用 `save_tuniu_api_key` 保存。
- **Agent 会在执行 `tuniu call` 前自动注入当前用户的 `TUNIU_API_KEY`**，命令中无需也不应硬编码真实 Key。
- 不要明文复述密钥（只用脱敏形式如 `sk-****abcd`）；不要代替用户执行含真实密钥的 shell 命令。

## 意图识别速查表（用户说什么 → 用什么工具）

| 用户意图关键词 | server | 首选工具 | 必填参数 |
|---------------|--------|----------|----------|
| 航班/机票/飞机 | `flight` | `searchLowestPriceFlight` | `departureCityName`, `arrivalCityName`, `departureDate` |
| 酒店/住宿/民宿 | `hotel` | `tuniuHotelSearch` | `cityName` |
| 火车票/高铁/动车 | `train` | `searchLowestPriceTrain` | `departureCityName`, `arrivalCityName`, `departureDate` |

## 基本命令格式

（无需设置 TUNIU_API_KEY，Agent 自动注入）

```bash
tuniu call <server> <tool> -a '<JSON参数>'
```

| 参数 | 说明 |
|------|------|
| `server` | 服务名称：`hotel`、`flight`、`train`、`traveler` |
| `tool` | 工具名称，如 `tuniuHotelSearch`、`searchLowestPriceFlight` 等 |
| `--args` 或 `-a` | 工具输入参数，必须是合法 JSON 字符串，用引号包裹；中文可直接写入；无参数用 `-a '{}'` |

## 服务工具链路（搜索 → 详情 → 下单）

| 服务 | 完整流程 |
|------|----------|
| `flight` | `searchLowestPriceFlight` → `multiCabinDetails` → `saveOrder` → `cancelOrder` |
| `hotel` | `tuniuHotelSearch` → `tuniuHotelDetail` → `tuniuHotelCreateOrder` |
| `train` | `searchLowestPriceTrain` → `queryTrainDetail` → `bookTrain` → `cancelOrder` |

> **下单前必须先调用搜索/详情接口**获取必需的中间参数（如 `cabinPriceId`、`resId`、`preBookParam` 等）。各服务完整必填参数与示例见对应的 `references/flight.md`、`references/hotel.md`、`references/train.md`。

## 常用辅助命令

| 命令 | 用途 |
|------|------|
| `tuniu list` / `tuniu list <server>` | 列出服务/工具 |
| `tuniu help <server> <tool>` | 查看参数说明 |
| `tuniu schema --output json` | 获取完整 Schema |
| `tuniu discovery refresh`，然后 `tuniu discovery list` | 检查新服务（分两次执行，禁用 `&&`） |
| `tuniu call ... -d` | 调试模式 |

## 服务发现

用户需求超出机票/酒店/火车票范围（如签证、租车），或工具返回退出码 102（工具不存在），或首次使用时，**必须**先执行：

```bash
# 分两次执行，不要用 && 串联
tuniu discovery refresh
tuniu discovery list
```

执行后重新检查服务列表再决定下一步。详细规则与最佳实践见 `references/common.md`。

## 隐私与个人信息（PII）

预订功能会将用户个人信息（联系人姓名、手机号、乘客姓名、证件号等）通过 tuniu CLI 发送至途牛远端服务以完成下单。请勿在日志或回复中暴露用户个人信息。

## 个人信息获取规则

所有涉及下单/预订的工具在需要用户个人信息时，**必须优先调用 `query_user_contact_info` 从用户档案中查询**：

1. 调用 `query_user_contact_info`（无需参数）。
2. 判断档案是否完整：
   - 酒店预订：看 `hotelComplete=true`（需要 `namePinyin` + `email`，`namePinyin` 会拆成 `lastName`/`firstName`）。
   - 机票/火车票等需乘客信息的场景：看 `flightComplete=true`（需要 `chineseName` + `idType` + `idNumber` + `phone` + `gender`）。
3. 若完整：直接复用档案字段，**不要重复索要**。
4. 若缺失：根据返回的 `message` 向用户追问缺少项，**禁止编造任何个人信息**。
5. 用户提供新信息后，**必须调用 `update_user_contact_info` 保存**，后续预订自动复用。

### 通用字段映射

| query_user_contact_info 字段 | 途牛下单字段含义 | 说明 |
|---|---|---|
| `chineseName` | 中文姓名（乘机人/游客/联系人） | 用于航班、火车票的 `name` |
| `namePinyin` / `lastName` + `firstName` | 酒店入住人英文姓/名 | 酒店 `roomGuests[].lastName` ← `lastName`，`roomGuests[].firstName` ← `firstName` |
| `email` | 联系邮箱 | 酒店等场景的联系人邮箱 |
| `phone` | 手机号 | 各服务的 `mobile` / `contactPhone` / `tel` |
| `idType` / `idTypeLabel` | 证件类型 | 各接口要求不一致，见下方「跨接口参数类型/编码不一致对照表」 |
| `idNumber` | 证件号码 | 各服务的证件号字段 |
| `gender` | 性别 | 需要时映射为 `M`/`F` |

> 用户档案中 `idType` 是整数代码（0-身份证，1-护照…），`idTypeLabel` 是对应中文。各服务下单字段的具体映射与示例见对应服务的 references 文档。

### ⚠️ 跨接口参数类型/编码不一致对照表（务必按此填写，切勿靠报错试错）

途牛不同接口对**同一含义字段**要求的类型/编码并不一致，且接口侧无法统一。下表为权威约定，调用前直接按此组织参数；**不要因某接口成功就把同样写法套用到另一接口**。

**1）酒店 `hotelId` 类型（number ↔ string 不一致）**

| 工具 | `hotelId` 类型 | 示例 |
|------|---------------|------|
| `tuniuHotelDetail` | **数字 number** | `{"hotelId":214076776}` |
| `tuniuHotelCreateOrder` | **字符串 string** | `{"hotelId":"214076776"}` |

> 同一酒店：查详情用数字、下单用字符串，二者不可混用。若报 `Expected number, received string` 说明该接口要数字；报 `Expected string, received number` 说明该接口要字符串——按上表一次填对，不要来回切换试。

**2）证件类型（字符串中文 ↔ 数字编码 不一致）**

| 服务 | 下单字段 | 类型 | 身份证 | 护照 | 取值来源 |
|------|---------|------|--------|------|---------|
| 机票 `saveOrder` | `tourists[].idType` | 字符串 | `"身份证"` | `"护照"` | 直接取 `idTypeLabel` |
| 火车 `bookTrain` | `psptType` | 数字 | `1` | `2` | 由 `idTypeLabel` 映射：身份证→`1`，护照→`2` |

> 用户档案 `idType` 编码为 `0=身份证 / 1=护照`，与火车 `psptType`（`1=身份证 / 2=护照`）**不同**，切勿把档案 `idType` 直接透传给 `psptType`（透传 `0` 会报「证件类型不能为空」）。请统一以 `idTypeLabel`（中文名）为基准，再按上表转换到对应接口所需的类型/编码。

## 适用场景

- 机票搜索、舱位查询、机票预订
- 酒店搜索、详情查询、酒店预订
- 火车票车次查询、车次详情、火车票预订
- **动态服务发现**：需求超出上述范围时，通过 discovery 检查是否有新服务上线

## 注意事项

1. **密钥安全**：不要在回复或日志中暴露完整 TUNIU_API_KEY；引导用户通过 `save_tuniu_api_key` 保存。
2. **PII 安全**：联系人/乘客个人信息仅在预订时发送至途牛服务，勿在日志或回复中暴露。
3. **认证错误**（退出码 104、108）：先调用 `check_tuniu_api_key`；未配置时引导用户获取并保存。
4. **日期格式**：所有日期均为 `YYYY-MM-dd` 或 `yyyy-MM-dd`。
5. **参数验证**：下单前必须先调用搜索/详情接口获取必需参数。
6. **翻页**：各服务翻页参数不同，注意区分（详见对应服务的 references 文档）。
7. **支付链接返回（重要）**：下单成功后**必须把支付链接返回给用户**，不能只回复订单号。从 `result` 中取 `payUrl`/`paymentUrl`/`cashierUrl`/`orderDetailH5Url`/`orderDetailUrl` 等字段中存在者，以 Markdown 链接输出，如 `请点击 [立即支付](https://...) 完成付款`；确无任何链接字段时才只回复订单号并提示前往途牛订单中心支付。
8. **订单结果提示**：下单成功后明确展示 `orderId` 与应付金额，并附上第 7 点的支付链接，提醒用户完成付款后可在途牛 App/小程序跟进订单与出行通知。
9. **102 处理**：若错误 JSON 含 `error.details.available_tools`，优先从中选真实工具名重试；否则执行 `tuniu list <server> -o json` 获取工具名，再用 `tuniu help`/`tuniu schema` 确认参数。禁止继续用错误工具名重试。
