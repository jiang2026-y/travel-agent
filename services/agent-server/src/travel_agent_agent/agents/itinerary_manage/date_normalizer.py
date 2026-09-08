# 本文件实现行程管理工具使用的中国业务日期规范化。
# 定义 TravelDateNormalizer、TravelDateError 和 DateRange，统一输出 PostgreSQL DATE 值。
from __future__ import annotations

import calendar
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_TIME_ZONE = ZoneInfo("Asia/Shanghai")
_WEEKDAY_MAP = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


class TravelDateError(ValueError):
    """表示相对日期无法规范化、日期非法或日期范围不合法。"""


@dataclass(frozen=True, slots=True)
class DateRange:
    """保存已规范化的差旅开始和结束日期。"""

    departure_date: date
    return_date: date


class TravelDateNormalizer:
    """按 Asia/Shanghai 业务日历解析相对日期并输出 DATE。"""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        """注入可替换时钟，保证日期边界测试可重复。"""
        self._clock = clock or (lambda: datetime.now(_TIME_ZONE))

    def normalize(self, value: str, current: date | None = None) -> date:
        """将一个明确或相对日期表达规范化为 DATE。"""
        text = value.strip()
        now = self._clock()
        china_now = (
            now.replace(tzinfo=_TIME_ZONE)
            if now.tzinfo is None
            else now.astimezone(_TIME_ZONE)
        )
        today = current or china_now.date()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            try:
                return date.fromisoformat(text)
            except ValueError as error:
                raise TravelDateError("invalid_date") from error
        relative = {"明天": 1, "后天": 2, "大后天": 3}
        if text in relative:
            return today + timedelta(days=relative[text])
        days_match = re.fullmatch(r"([0-9一二三四五六七八九十]+)天后", text)
        if days_match:
            days = self._parse_number(days_match.group(1))
            return today + timedelta(days=days)
        weekday_match = re.fullmatch(r"下周([一二三四五六日天])", text)
        if weekday_match:
            target = _WEEKDAY_MAP[weekday_match.group(1)]
            current_monday = today - timedelta(days=today.weekday())
            return current_monday + timedelta(days=7 + target)
        if text == "月底":
            return date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
        day_match = re.fullmatch(r"(\d{1,2})号", text)
        if day_match:
            day = int(day_match.group(1))
            return self._next_matching_day(today, day)
        raise TravelDateError("date_expression_ambiguous")

    def normalize_range(
        self, departure: str, returning: str, current: date | None = None
    ) -> DateRange:
        """规范化往返日期并拒绝结束早于开始的行程。"""
        departure_date = self.normalize(departure, current)
        return_date = self.normalize(returning, current)
        if return_date < departure_date:
            raise TravelDateError("return_before_departure")
        return DateRange(departure_date, return_date)

    @staticmethod
    def _parse_number(value: str) -> int:
        """解析一到十的中文数字或阿拉伯数字。"""
        if value.isdigit():
            return int(value)
        parsed = _CHINESE_NUMBERS.get(value)
        if parsed is None:
            raise TravelDateError("relative_day_count_invalid")
        return parsed

    @staticmethod
    def _next_matching_day(today: date, day: int) -> date:
        """按本月优先、已过则下月的规则解析仅含日号表达。"""
        if not 1 <= day <= 31:
            raise TravelDateError("day_of_month_invalid")
        next_year = today.year + (1 if today.month == 12 else 0)
        next_month = 1 if today.month == 12 else today.month + 1
        for year, month in ((today.year, today.month), (next_year, next_month)):
            maximum = calendar.monthrange(year, month)[1]
            if day > maximum:
                continue
            candidate = date(year, month, day)
            if candidate >= today:
                return candidate
        raise TravelDateError("day_of_month_invalid")
