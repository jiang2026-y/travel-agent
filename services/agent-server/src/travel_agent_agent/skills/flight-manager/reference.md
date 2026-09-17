# 智程机票 MCP — 数据字典

## 证件类型 idType

| 代码 | 类型 |
|------|------|
| 0 | 身份证（仅限国内） |
| 1 | 护照 |
| 2 | 其他 |
| 3 | 回乡证 |
| 4 | 军官证 |
| 5 | 警官证 |
| 6 | 港澳通行证 |
| 7 | 台胞证 |
| 8 | 台湾通行证 |
| 9 | 外国人永久居留身份证 |

> 国际机票 `idType` 不能为 0。非身份证时，`birthday`、`nationality`、`issueNa`、`validDate` 必填。

## 舱位等级 cabinPreference

| 代码 | 舱位 |
|------|------|
| Y | 经济舱 |
| S | 超级经济舱 |
| C | 商务舱 |
| F | 头等舱 |

## 退票原因 refundReasonType

**国内：** 0=航班延误、1=航班备降、2=航班取消、3=航司会员退票、4=病退、5=其他

**国际：** 1=航班延误/取消/备降、2=因伤病无法出行、3=重复购买机票、4=航班拒签、5=航司优惠退、6=其他

## 改签筛选 filterType

| 代码 | 含义 |
|------|------|
| 0 | 不筛选 |
| 1 | 按时间最近 |
| 2 | 按价格差最少 |
| 3 | 同时筛选 |

## 常用城市三字码

| 代码 | 城市 |
|------|------|
| AAT | 阿勒泰 |
| AKA | 安康 |
| AKU | 阿克苏 |
| AOG | 鞍山 |
| AQG | 安庆 |
| AYN | 安阳 |
| BAV | 包头 |
| BFU | 蚌埠 |
| BHY | 北海 |
| BJS | 北京 |
| BSD | 保山 |
| CAN | 广州 |
| CCC | 潮州 |
| CGD | 常德 |
| CGO | 郑州 |
| CGQ | 长春 |
| CHG | 朝阳 |
| CHW | 酒泉 |
| CIF | 赤峰 |
| CIH | 长治 |
| CKG | 重庆 |
| CNI | 长海 |
| CSX | 长沙 |
| CTU | 成都 |
| CZX | 常州 |
| DAT | 大同 |
| DAX | 达县 |
| DDG | 丹东 |
| DIG | 迪庆香格里拉 |
| DLC | 大连 |
| DLU | 大理 |
| DNH | 敦煌 |
| DYG | 张家界 |
| ENH | 恩施 |
| ENY | 延安 |
| FOC | 福州 |
| FUG | 阜阳 |
| FUO | 佛山 |
| GHN | 广汉 |
| GOQ | 格尔木 |
| HAK | 海口 |
| HEI | 呼和浩特 |
| HEK | 黑河 |
| HFE | 合肥 |
| HGH | 杭州 |
| HHA | 长沙/黄花 |
| HLD | 海拉尔 |
| HLH | 乌兰浩特 |
| HMI | 哈密 |
| HNY | 衡阳 |
| HRB | 哈尔滨 |
| HSN | 舟山 |
| HTN | 和田 |
| HUZ | 徽州 |
| HYN | 黄岩 |
| HZG | 汉中 |
| INC | 银川 |
| IQN | 庆阳 |
| JDZ | 景德镇 |
| JGN | 嘉峪关 |
| JHG | 西双版纳 |
| JIL | 吉林 |
| JIU | 九江 |
| JJN | 泉州晋江 |
| JMU | 佳木斯 |
| JNG | 济宁 |
| JNZ | 锦州 |
| JUZ | 衢州 |
| KCA | 库车 |
| KHG | 喀什 |
| KHN | 南昌 |
| KMG | 昆明 |
| KNC | 吉安 |
| KOW | 赣州 |
| KRL | 库尔勒 |
| KRY | 克拉玛依 |
| KWE | 贵阳 |
| KWL | 桂林 |
| LHW | 兰州 |
| LIA | 梁平 |
| LJG | 丽江 |
| LUM | 德宏芒市 |
| LUZ | 庐山 |
| LXA | 拉萨 |
| LXI | 林西 |
| LYA | 洛阳 |
| LYG | 连云港 |
| LYI | 临沂 |
| LZD | 兰州东 |
| LZH | 柳州 |
| LZO | 泸州 |
| MDG | 牡丹江 |
| MIG | 绵阳 |
| MXZ | 梅县 |
| NAO | 南充 |
| NDG | 齐齐哈尔 |
| NGB | 宁波 |
| NKG | 南京 |
| NNG | 南宁 |
| NNY | 南阳 |
| NTG | 南通 |
| PEK | 北京首都机场 |
| PKX | 北京大兴机场 |
| PVG | 上海浦东 |
| SHA | 上海虹桥 |
| SHE | 沈阳 |
| SHP | 秦皇岛 |
| SHS | 沙市 |
| SIA | 西安 |
| SWA | 汕头 |
| SYM | 思茅 |
| SYX | 三亚 |
| SZX | 深圳 |
| TAO | 青岛 |
| TEN | 铜仁 |
| TGO | 通辽 |
| TNA | 济南 |
| TSN | 天津 |
| TXN | 黄山 |
| TYN | 太原 |
| URC | 乌鲁木齐 |
| UYN | 榆林 |
| WEF | 潍坊 |
| WEH | 威海 |
| WNZ | 温州 |
| WUH | 武汉 |
| WUS | 武夷山 |
| WXN | 万州 |
| XEN | 兴城 |
| XFN | 襄樊 |
| XIC | 西昌 |
| XIL | 锡林浩特 |
| XIN | 兴宁 |
| XIY | 西安咸阳机场 |
| XMN | 厦门 |
| XNN | 西宁 |
| XUZ | 徐州 |
| YBP | 宜宾 |
| YCU | 运城 |
| YIH | 宜昌 |
| YIN | 伊宁 |
| YIW | 义乌 |
| YNJ | 延吉 |
| YNT | 烟台 |
| ZAT | 昭通 |
| ZHA | 湛江 |
| ZHD | 中甸 |
| ZUH | 珠海 |
| ZYI | 遵义 |


## 通用约定

- 日期格式：`YYYY-MM-DD`
- 货币：CNY，精确到分
- 排序字段：`price`、`totalPrice`、`departureTime`、`arrivalTime`、`duration`
- 排序顺序：`asc`、`desc`
- 分页：`page`（默认 1）、`pageSize`（默认 5）
