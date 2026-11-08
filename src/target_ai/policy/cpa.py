"""Сравнение цены результата без деления и без float: всё в целых и дробях."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from target_ai.money import Money


def spend_at_least(spend: Money | None, target: Money, multiple: Decimal) -> bool:
    """Расход ≥ multiple × целевая цена."""
    return spend is not None and Fraction(spend.amount_minor) >= Fraction(multiple) * target.amount_minor


def cpa_at_least(spend: Money | None, results: int, target: Money, multiple: Decimal) -> bool:
    """Цена результата ≥ multiple × целевая; ноль результатов — цена бесконечна."""
    if spend is None:
        return False
    if results <= 0:
        return True
    return Fraction(spend.amount_minor, results) >= Fraction(multiple) * target.amount_minor


def cpa_at_most(spend: Money | None, results: int, target: Money, multiple: Decimal) -> bool:
    """Цена результата ≤ multiple × целевая; без результатов — никогда."""
    if spend is None or results <= 0:
        return False
    return Fraction(spend.amount_minor, results) <= Fraction(multiple) * target.amount_minor


def cpa_money(spend: Money, results: int) -> Money:
    """Цена результата, округлённая вниз до минимальной единицы (для текста причин)."""
    return Money(spend.amount_minor // results, spend.currency)


def strength_key(spend: Money | None, results: int) -> Fraction:
    """Чем больше значение, тем слабее: цена результата, а без результатов — бесконечность."""
    if spend is None or results <= 0:
        return Fraction(10**18)
    return Fraction(spend.amount_minor, results)
