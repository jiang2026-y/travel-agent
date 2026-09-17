# 文件职责：验证差旅城市等级配置的加载、校验和衔接时间优先级。
# 定义配置加载、方向无关城市对和默认等级阈值测试。
from __future__ import annotations

from pathlib import Path

import pytest

from travel_agent_api.config.city_tier import (
    CityTierConfigError,
    load_city_tier_config,
)

_CONFIG = Path(__file__).parents[2] / "src" / "travel_agent_api" / "config" / "city-tier.yml"


def test_city_tier_config_loads_and_uses_explicit_pair() -> None:
    config = load_city_tier_config(_CONFIG)

    assert config.transit_minutes("北京", "上海") == 300
    assert config.transit_minutes("上海", "北京") == 300


def test_city_tier_config_uses_tier_defaults() -> None:
    config = load_city_tier_config(_CONFIG)

    assert config.transit_minutes("深圳", "重庆") == 270
    assert config.transit_minutes("济南", "福州") == 300
    assert config.transit_minutes("未配置城市", "另一座城市") == 360


def test_city_tier_config_rejects_duplicate_pair(tmp_path: Path) -> None:
    config_path = tmp_path / "city-tier.yml"
    config_path.write_text(
        """
travel:
  policy:
    tier1-cities: [北京]
    new-tier1-cities: [杭州]
    tier2-cities: [济南]
    city-pair-minutes:
      '[北京-杭州]': 300
      '[杭州-北京]': 300
""",
        encoding="utf-8",
    )

    with pytest.raises(CityTierConfigError, match="city_pair_duplicate"):
        load_city_tier_config(config_path)


def test_city_tier_config_rejects_invalid_minutes(tmp_path: Path) -> None:
    config_path = tmp_path / "city-tier.yml"
    config_path.write_text(
        """
travel:
  policy:
    tier1-cities: [北京]
    new-tier1-cities: [杭州]
    tier2-cities: [济南]
    city-pair-minutes:
      '[北京-杭州]': 0
""",
        encoding="utf-8",
    )

    with pytest.raises(CityTierConfigError, match="city_pair_minutes_must_be_positive"):
        load_city_tier_config(config_path)
