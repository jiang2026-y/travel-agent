# 文件职责：定义差旅偏好设置页面的唯一选项目录（对齐 Java PreferenceController）。
# 定义 PreferenceItem、PreferenceCategory 与 TRAVEL_PREFERENCE_CATALOG，
# 以及 flatten_catalog、format_preference_sentences、catalog_key_options 辅助函数。
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PreferenceItem:
    """保存一个偏好项：稳定 key、中文标签、单选/多选与可选值。"""

    key: str
    label: str
    kind: str
    options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreferenceCategory:
    """保存一个偏好分类：分类标识、中文标签、图标与若干偏好项。"""

    category: str
    label: str
    icon: str
    items: tuple[PreferenceItem, ...]


TRAVEL_PREFERENCE_CATALOG: tuple[PreferenceCategory, ...] = (
    PreferenceCategory(
        "flight",
        "机票偏好",
        "✈️",
        (
            PreferenceItem(
                "flight_cabin", "舱位偏好", "single", ("经济舱", "超级经济舱", "公务舱", "头等舱")
            ),
            PreferenceItem(
                "flight_airline",
                "偏好航司",
                "multi",
                (
                    "国航(CA)",
                    "东航(MU)",
                    "南航(CZ)",
                    "海航(HU)",
                    "厦航(MF)",
                    "深航(ZH)",
                    "川航(3U)",
                    "春秋(9C)",
                    "吉祥(HO)",
                    "山航(SC)",
                ),
            ),
            PreferenceItem(
                "flight_seat",
                "座位位置",
                "single",
                ("靠窗", "靠过道", "前排", "紧急出口排", "无偏好"),
            ),
            PreferenceItem(
                "flight_time",
                "航班时间",
                "single",
                (
                    "早班(6:00-9:00)",
                    "上午(9:00-12:00)",
                    "下午(12:00-18:00)",
                    "晚班(18:00-21:00)",
                    "红眼航班也可以",
                    "无偏好",
                ),
            ),
            PreferenceItem(
                "flight_direct",
                "中转偏好",
                "single",
                ("只选直飞", "可接受一次中转", "价格优先不限中转", "无偏好"),
            ),
        ),
    ),
    PreferenceCategory(
        "hotel",
        "酒店偏好",
        "🏨",
        (
            PreferenceItem(
                "hotel_star",
                "星级偏好",
                "single",
                ("经济型", "舒适型(三星)", "高档型(四星)", "豪华型(五星)", "无偏好"),
            ),
            PreferenceItem(
                "hotel_brand",
                "偏好品牌",
                "multi",
                (
                    "全季",
                    "亚朵",
                    "如家商旅",
                    "汉庭",
                    "维也纳",
                    "桔子",
                    "希尔顿",
                    "万豪",
                    "洲际",
                    "凯悦",
                    "香格里拉",
                    "华住",
                ),
            ),
            PreferenceItem("hotel_room", "房型偏好", "single", ("大床房", "双床房", "无偏好")),
            PreferenceItem(
                "hotel_floor", "楼层偏好", "single", ("高楼层", "低楼层(方便出行)", "无偏好")
            ),
            PreferenceItem(
                "hotel_location",
                "位置偏好",
                "multi",
                ("靠近办公/会议地点", "靠近地铁/交通枢纽", "靠近市中心", "安静环境", "有停车场"),
            ),
            PreferenceItem(
                "hotel_facilities",
                "设施需求",
                "multi",
                ("健身房", "早餐", "免费Wi-Fi", "商务中心", "洗衣服务", "接机服务"),
            ),
        ),
    ),
    PreferenceCategory(
        "train",
        "高铁/火车偏好",
        "🚄",
        (
            PreferenceItem(
                "train_seat", "座位等级", "single", ("二等座", "一等座", "商务座", "无偏好")
            ),
            PreferenceItem(
                "train_time",
                "出发时段",
                "single",
                (
                    "早班(6:00-9:00)",
                    "上午(9:00-12:00)",
                    "下午(12:00-18:00)",
                    "晚班(18:00-21:00)",
                    "无偏好",
                ),
            ),
            PreferenceItem("train_position", "座位位置", "single", ("靠窗", "靠过道", "无偏好")),
        ),
    ),
    PreferenceCategory(
        "general",
        "出行习惯",
        "🧳",
        (
            PreferenceItem(
                "transport",
                "市内交通",
                "single",
                ("地铁/公交优先", "打车优先", "自驾/租车", "无偏好"),
            ),
            PreferenceItem(
                "reimburse_priority",
                "费用敏感度",
                "single",
                (
                    "价格优先(尽量省钱)",
                    "性价比优先(合理范围选舒适)",
                    "体验优先(预算内最舒适)",
                    "无偏好",
                ),
            ),
            PreferenceItem(
                "schedule_priority",
                "时间安排",
                "single",
                ("尽量当天往返", "提前一晚到达", "会议/办事结束当天返回", "灵活安排", "无偏好"),
            ),
            PreferenceItem(
                "meal",
                "餐饮偏好",
                "multi",
                ("无特殊要求", "清淡饮食", "素食", "清真", "无辣", "不含海鲜", "无麸质"),
            ),
        ),
    ),
)


def catalog_payload() -> list[dict[str, object]]:
    """返回可直接序列化给前端的目录结构。"""
    return [
        {
            "category": category.category,
            "label": category.label,
            "icon": category.icon,
            "items": [
                {
                    "key": item.key,
                    "label": item.label,
                    "type": item.kind,
                    "options": list(item.options),
                }
                for item in category.items
            ],
        }
        for category in TRAVEL_PREFERENCE_CATALOG
    ]


def catalog_key_options() -> dict[str, list[str]]:
    """返回解析用的 key → 允许取值，用于约束模型输出。"""
    return {
        item.key: list(item.options)
        for category in TRAVEL_PREFERENCE_CATALOG
        for item in category.items
    }


def format_preference_sentences(preferences: dict[str, list[str]]) -> list[str]:
    """把结构化勾选格式化为可直接写入长期记忆的中文偏好句子。"""
    sentences: list[str] = []
    for category in TRAVEL_PREFERENCE_CATALOG:
        for item in category.items:
            raw = preferences.get(item.key)
            if raw is None:
                continue
            values = [raw] if isinstance(raw, str) else list(raw)
            kept = [
                str(value) for value in values if isinstance(value, str) and value in item.options
            ]
            if not kept:
                continue
            sentences.append(f"用户出差偏好：{category.label}-{item.label}=" + "、".join(kept))
    return sentences
