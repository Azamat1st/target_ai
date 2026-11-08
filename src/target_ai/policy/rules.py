"""Пороги политики: значения по умолчанию из спеки §3 и §6."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Meta в отдельный день может потратить больше дневного бюджета (спека §6, консервативно).
META_DAILY_OVERSPEND = Decimal("0.75")

# Снижение бюджета группы — не ниже потраченного сегодня + 10% (правило Meta).
BUDGET_FLOOR_OVER_SPENT_PCT = 10

MIN_RETARGETING_AUDIENCE = 1_000
MIN_LOOKALIKE_SOURCE = 1_000
NARROW_AUDIENCE_SIZE = 50_000
NARROW_AUDIENCE_SIZE_SMALL_RADIUS = 10_000
SMALL_RADIUS_KM = 10
MIN_GROUPS = 2
MAX_GROUPS = 4
MIN_ADS_PER_GROUP = 2
MAX_ADS_PER_GROUP = 3
MAX_INTERESTS_PER_GROUP = 10
INTEREST_OVERLAP_SHARE = Decimal("0.5")


@dataclass(frozen=True)
class OptimizerRules:
    """Правила оптимизатора (R54–R58); хранятся в настройках, проверяются при создании."""

    min_hours_before_decision: int = 72
    min_spend_target_cpa_multiple: Decimal = Decimal("2.0")
    budget_change_max_pct: int = 20
    budget_change_min_interval_hours: int = 24
    kill_cpa_multiple: Decimal = Decimal("2.0")
    strong_cpa_ratio: Decimal = Decimal("1.0")
    fatigue_frequency: Decimal = Decimal("3.0")
    weak_ad_cpa_multiple: Decimal = Decimal("2.0")
    weak_ad_min_impressions: int = 1_000

    def __post_init__(self) -> None:
        if self.min_hours_before_decision < 0 or self.budget_change_min_interval_hours < 0:
            raise ValueError("Часы в правилах не могут быть отрицательными.")
        if not 0 < self.budget_change_max_pct <= 100:
            raise ValueError("Максимальное изменение бюджета — от 1 до 100 процентов.")
        positive = (
            self.min_spend_target_cpa_multiple, self.kill_cpa_multiple,
            self.strong_cpa_ratio, self.fatigue_frequency, self.weak_ad_cpa_multiple,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("Множители и порог частоты в правилах должны быть положительными.")
        if self.weak_ad_min_impressions < 0:
            raise ValueError("Порог показов не может быть отрицательным.")
