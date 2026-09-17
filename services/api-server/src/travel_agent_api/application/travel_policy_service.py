# 文件职责：按用户职级和城市等级查询差旅政策，并执行费用合规校验。
# 定义 TravelPolicyService、PolicyServiceError 及职级/舱位解析辅助函数。
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travel_agent_api.config.city_tier import CityTierConfig
from travel_agent_api.persistence.database import metadata
from travel_agent_api.persistence.models import TravelPolicyRule, User

_USER_COLUMNS = metadata.tables["users"].c
_RULE_COLUMNS = metadata.tables["travel_policy_rules"].c


class PolicyServiceError(RuntimeError):
    """表示用户职级无效或数据库中不存在可匹配的政策。"""


_LEVEL_PATTERN = re.compile(r"^P?\s*(\d+)\s*(?:\+|及以上)?$", re.IGNORECASE)
_FLIGHT_RANK = {"ECONOMY": 1, "经济舱": 1, "BUSINESS": 2, "商务舱": 2, "FIRST": 3, "头等舱": 3}
_TRAIN_RANK = {"SECOND": 1, "二等座": 1, "FIRST": 2, "一等座": 2}


class TravelPolicyService:
    """通过 PostgreSQL 查询当前用户可适用的差旅政策。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        city_tier_config: CityTierConfig,
    ) -> None:
        """保存数据库会话工厂和城市等级配置。"""
        self.session_factory = session_factory
        self.city_tier_config = city_tier_config

    async def get_policy(self, user_id: str, city: str) -> dict[str, Any]:
        """查询当前用户职级和指定城市对应的政策规则。"""
        if not city.strip():
            raise PolicyServiceError("city_required")
        async with self.session_factory() as session:
            user = await session.scalar(
                select(User).where(_USER_COLUMNS.user_id == user_id)
            )
            if user is None:
                raise PolicyServiceError("profile_not_found")
            level = parse_level(user.level)
            if level is None:
                raise PolicyServiceError("profile_incomplete")
            tier = self.city_tier_config.city_tier(city)
            rule = await session.scalar(
                select(TravelPolicyRule).where(
                    _RULE_COLUMNS.level_min <= level,
                    _RULE_COLUMNS.level_max >= level,
                    _RULE_COLUMNS.city_tier == tier,
                )
            )
        if rule is None:
            raise PolicyServiceError("policy_not_found")
        return policy_record(rule, level, tier)

    async def check_policy(self, user_id: str, city: str, values: dict[str, Any]) -> dict[str, Any]:
        """查询规则并返回所有可识别的合规违规项，不执行写操作。"""
        policy = await self.get_policy(user_id, city)
        violations: list[dict[str, Any]] = []
        _check_decimal_limit(
            violations,
            "hotel_amount",
            values.get("hotel_amount"),
            policy["hotel_limit"],
            "酒店费用超过住宿标准",
        )
        _check_integer_limit(
            violations,
            "hotel_star",
            values.get("hotel_star"),
            policy["hotel_star_limit"],
            "酒店星级超过允许上限",
        )
        _check_rank_limit(
            violations,
            "flight_class",
            values.get("flight_class"),
            policy["flight_class"],
            _FLIGHT_RANK,
            "机票舱位超过允许等级",
        )
        _check_rank_limit(
            violations,
            "train_seat_class",
            values.get("train_seat_class"),
            policy["train_seat_class"],
            _TRAIN_RANK,
            "火车座席超过允许等级",
        )
        _check_decimal_limit(
            violations,
            "daily_meal_amount",
            values.get("daily_meal_amount"),
            policy["daily_meal_limit"],
            "餐补超过每日标准",
        )
        _check_decimal_limit(
            violations,
            "daily_transport_amount",
            values.get("daily_transport_amount"),
            policy["daily_transport_limit"],
            "交通补贴超过每日标准",
        )
        total_amount = _to_decimal(values.get("amount"))
        requires_escalation = total_amount is not None and total_amount > Decimal(
            str(policy["approval_threshold"])
        )
        departure_date = values.get("departure_date")
        if isinstance(departure_date, date):
            days = (departure_date - datetime.now(ZoneInfo("Asia/Shanghai")).date()).days
            if days < policy["advance_booking_days"]:
                violations.append(
                    {
                        "field": "departure_date",
                        "actual": departure_date.isoformat(),
                        "limit": policy["advance_booking_days"],
                        "message": "出发日期未满足提前预订要求",
                    }
                )
        return {
            "status": "ok",
            "compliant": not violations,
            "requires_escalation": requires_escalation,
            "violations": violations,
            "policy": policy,
        }


def parse_level(level: str | None) -> int | None:
    """解析 P1、P10、P11+ 或纯数字职级。"""
    if not level:
        return None
    match = _LEVEL_PATTERN.fullmatch(level.strip())
    parsed = int(match.group(1)) if match else 0
    return parsed if parsed > 0 else None


def policy_record(rule: TravelPolicyRule, level: int, tier: str) -> dict[str, Any]:
    """将数据库模型转换为不含用户身份的政策响应。"""
    return {
        "level_group": level_group(level),
        "city_tier": tier,
        "flight_class": rule.flight_class,
        "train_seat_class": rule.train_seat_class,
        "hotel_limit": float(rule.hotel_limit),
        "hotel_star_limit": rule.hotel_star_limit,
        "daily_meal_limit": float(rule.daily_meal_limit),
        "daily_transport_limit": float(rule.daily_transport_limit),
        "approval_threshold": float(rule.approval_threshold),
        "advance_booking_days": rule.advance_booking_days,
    }


def level_group(level: int) -> str:
    """返回制度文档中的职级分组名称。"""
    if level <= 8:
        return "P1-P8"
    if level <= 10:
        return "P9-P10"
    return "P11+"


def _to_decimal(value: Any) -> Decimal | None:
    """将金额转换为 Decimal，无法解析时返回空值。"""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _check_decimal_limit(
    violations: list[dict[str, Any]], field: str, actual: Any, limit: float, message: str
) -> None:
    """检查金额字段是否超过政策上限。"""
    value = _to_decimal(actual)
    if value is not None and value > Decimal(str(limit)):
        violations.append(
            {"field": field, "actual": float(value), "limit": limit, "message": message}
        )


def _check_integer_limit(
    violations: list[dict[str, Any]], field: str, actual: Any, limit: int, message: str
) -> None:
    """检查整数等级字段是否超过政策上限。"""
    if actual is not None and int(actual) > limit:
        violations.append(
            {"field": field, "actual": int(actual), "limit": limit, "message": message}
        )


def _check_rank_limit(
    violations: list[dict[str, Any]],
    field: str,
    actual: Any,
    allowed: str | None,
    ranks: dict[str, int],
    message: str,
) -> None:
    """按标准等级顺序检查机票或火车座席。"""
    if actual is None or allowed is None:
        return
    actual_rank = ranks.get(str(actual).strip().upper()) or ranks.get(str(actual).strip())
    allowed_rank = ranks.get(str(allowed).strip().upper()) or ranks.get(str(allowed).strip())
    if actual_rank is None or allowed_rank is None:
        violations.append(
            {
                "field": field,
                "actual": str(actual),
                "limit": allowed,
                "message": f"{field}等级无法识别",
            }
        )
    elif actual_rank > allowed_rank:
        violations.append(
            {"field": field, "actual": str(actual), "limit": allowed, "message": message}
        )
