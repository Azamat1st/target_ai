"""Ключи настроек в базе (таблица settings) и их значения по умолчанию — единственное место, где они записаны.

Остальные модули импортируют отсюда. Правила оптимизатора и множитель перерасхода по умолчанию живут в
`policy` (`OptimizerRules`, `META_DAILY_OVERSPEND`): здесь только ключи, под которыми хранятся изменения.
"""

from dataclasses import dataclass
from decimal import Decimal

SETTING_LEVEL = "level"
LEVEL_DEMO = "demo"
DEFAULT_LEVEL = LEVEL_DEMO

SETTING_HALTED = "halted"
DEFAULT_HALTED = False

SETTING_RULES = "optimizer_rules"
SETTING_THRESHOLDS = "alert_thresholds"
SETTING_OVERSPEND = "overspend"
SETTING_INSTALL_ID = "install_id"

SETTING_REPORT_TIME = "report_time"  # "HH:MM"
DEFAULT_REPORT_TIME = "10:00"
SETTING_REPORT_TZ = "report_tz"
DEFAULT_TZ = "UTC"

SETTING_MONTH = "month:{}"  # формат: ключ на компанию, значение — последний обработанный месяц


@dataclass(frozen=True)
class Thresholds:
    """Пороги уведомлений (спека, история 58) — значения по умолчанию для ключа `alert_thresholds`."""

    no_impressions_hours: int = 6
    spend_spike_ratio: Decimal = Decimal("1.5")
    cpa_spike_ratio: Decimal = Decimal("2.0")
    token_days: int = 7
