# 文件职责：加载并校验差旅城市等级与城市对衔接时间配置。
# 定义 CityTierConfig、load_city_tier_config 及配置查询辅助方法。
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class CityTierConfigError(ValueError):
    """表示城市等级配置缺失、格式错误或内容不一致。"""


@dataclass(frozen=True, slots=True)
class CityTierConfig:
    """保存经过校验的城市等级和城市对最短衔接时间。"""

    tier1_cities: frozenset[str]
    new_tier1_cities: frozenset[str]
    tier2_cities: frozenset[str]
    city_pair_minutes: dict[frozenset[str], int]

    def city_tier(self, city: str | None) -> str:
        """将城市映射为政策表使用的一线、新一线或其他等级。"""
        normalized = _normalize_city(city)
        if normalized in self.tier1_cities:
            return "一线"
        if normalized in self.new_tier1_cities:
            return "新一线"
        return "其他"

    def transit_minutes(self, departure_city: str | None, arrival_city: str | None) -> int:
        """按显式城市对、城市等级和其他城市的优先级返回衔接分钟数。"""
        departure = _normalize_city(departure_city)
        arrival = _normalize_city(arrival_city)
        if departure and arrival and departure != arrival:
            explicit = self.city_pair_minutes.get(frozenset((departure, arrival)))
            if explicit is not None:
                return explicit
        if departure in self.tier1_cities or departure in self.new_tier1_cities:
            departure_default = 270
        elif departure in self.tier2_cities:
            departure_default = 300
        else:
            departure_default = 360
        if arrival in self.tier1_cities or arrival in self.new_tier1_cities:
            arrival_default = 270
        elif arrival in self.tier2_cities:
            arrival_default = 300
        else:
            arrival_default = 360
        return max(departure_default, arrival_default)


_DEFAULT_CONFIG_PATH = Path(__file__).with_name("city-tier.yml")
_CITY_LIST_KEYS = ("tier1-cities", "new-tier1-cities", "tier2-cities")


def load_city_tier_config(path: Path | None = None) -> CityTierConfig:
    """读取并严格校验城市配置；配置不可用时抛出启动级错误。"""
    config_path = path or _DEFAULT_CONFIG_PATH
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise CityTierConfigError(f"city_tier_config_unavailable:{config_path}") from error
    if not isinstance(raw, dict):
        raise CityTierConfigError("city_tier_config_root_invalid")
    travel = raw.get("travel")
    policy = travel.get("policy") if isinstance(travel, dict) else None
    if not isinstance(policy, dict):
        raise CityTierConfigError("city_tier_policy_missing")

    city_sets: dict[str, frozenset[str]] = {}
    all_cities: set[str] = set()
    for key in _CITY_LIST_KEYS:
        value = policy.get(key)
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(item, str) for item in value)
        ):
            raise CityTierConfigError(f"city_tier_list_invalid:{key}")
        normalized = frozenset(_normalize_city(item) for item in value)
        if "" in normalized or len(normalized) != len(value):
            raise CityTierConfigError(f"city_tier_list_duplicate_or_empty:{key}")
        if all_cities & set(normalized):
            raise CityTierConfigError(f"city_tier_city_duplicate:{key}")
        all_cities.update(normalized)
        city_sets[key] = normalized

    pair_value = policy.get("city-pair-minutes")
    if not isinstance(pair_value, dict):
        raise CityTierConfigError("city_pair_minutes_missing")
    pairs: dict[frozenset[str], int] = {}
    for raw_key, minutes in pair_value.items():
        if (
            not isinstance(raw_key, str)
            or not isinstance(minutes, int)
            or isinstance(minutes, bool)
        ):
            raise CityTierConfigError("city_pair_minutes_entry_invalid")
        if minutes <= 0:
            raise CityTierConfigError("city_pair_minutes_must_be_positive")
        if not raw_key.startswith("[") or not raw_key.endswith("]"):
            raise CityTierConfigError("city_pair_key_invalid")
        cities = raw_key[1:-1].split("-")
        if len(cities) != 2:
            raise CityTierConfigError("city_pair_key_invalid")
        left, right = (_normalize_city(city) for city in cities)
        if not left or not right or left == right:
            raise CityTierConfigError("city_pair_key_invalid")
        pair = frozenset((left, right))
        if pair in pairs:
            raise CityTierConfigError("city_pair_duplicate")
        pairs[pair] = minutes

    return CityTierConfig(
        tier1_cities=city_sets["tier1-cities"],
        new_tier1_cities=city_sets["new-tier1-cities"],
        tier2_cities=city_sets["tier2-cities"],
        city_pair_minutes=pairs,
    )


def _normalize_city(city: Any) -> str:
    """统一城市名称的空白，避免配置和数据库值因空格无法匹配。"""
    return city.strip() if isinstance(city, str) else ""
