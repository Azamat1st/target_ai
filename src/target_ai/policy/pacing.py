"""Темп расхода (спека §3, R49): дневной потолок из остатка лимита и оставшихся дней."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from fractions import Fraction

from target_ai.money import Money

from .rules import META_DAILY_OVERSPEND


def days_left(today: date) -> int:
    """Дней до конца месяца, включая сегодня."""
    last = calendar.monthrange(today.year, today.month)[1]
    return last - today.day + 1


def daily_cap(
    monthly_limit: Money,
    spent_month: Money,
    today: date,
    overspend: Decimal = META_DAILY_OVERSPEND,
) -> Money:
    """Максимальная сумма дневных бюджетов на сегодня; худший день не выводит за лимит."""
    remaining = max(0, (monthly_limit - spent_month).amount_minor)
    plain = remaining // days_left(today)
    factor = 1 + Fraction(overspend)
    if plain * factor > remaining:
        plain = int(Fraction(remaining) / factor)  # int() от неотрицательной дроби = floor
    return Money(plain, monthly_limit.currency)
