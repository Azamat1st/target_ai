from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from target_ai.money import CurrencyMismatch, Money
from target_ai.policy import META_DAILY_OVERSPEND, daily_cap, days_left


def usd(minor: int) -> Money:
    return Money(minor, "USD")


def test_overspend_default_is_conservative_075():
    assert META_DAILY_OVERSPEND == Decimal("0.75")


@pytest.mark.parametrize(
    ("today", "expected"),
    [(date(2026, 11, 1), 30), (date(2026, 11, 30), 1), (date(2026, 2, 1), 28), (date(2028, 2, 1), 29)],
)
def test_days_left_counts_today_to_month_end(today, expected):
    assert days_left(today) == expected


def test_first_day_splits_limit_over_whole_month():
    # 3 100,00 $ / 30 дней = 103,33 $ (округление вниз); 103,33 * 1,75 < остатка
    assert daily_cap(usd(310_000), usd(0), date(2026, 11, 1)) == usd(10_333)


def test_mid_month_uses_remaining_and_days_left_including_today():
    # остаток 600,00 $, с 16 по 30 ноября 15 дней → 40,00 $
    assert daily_cap(usd(100_000), usd(40_000), date(2026, 11, 16)) == usd(4_000)


def test_last_day_is_reduced_by_overspend_so_worst_day_stays_in_limit():
    # остаток 50,00 $, 1 день: 5000 * 1,75 > 5000 → floor(5000 / 1,75) = 2857
    assert daily_cap(usd(100_000), usd(95_000), date(2026, 11, 30)) == usd(2_857)


def test_overspent_previous_days_leave_nothing():
    assert daily_cap(usd(100_000), usd(130_000), date(2026, 11, 10)) == usd(0)


def test_nothing_remaining_gives_zero():
    assert daily_cap(usd(100_000), usd(100_000), date(2026, 11, 10)) == usd(0)


def test_zero_decimal_currency_floors_to_whole_units():
    jpy = lambda n: Money(n, "JPY")  # noqa: E731
    assert daily_cap(jpy(100_000), jpy(0), date(2026, 11, 1)) == jpy(3_333)
    assert daily_cap(jpy(1_000), jpy(0), date(2026, 11, 30)) == jpy(571)


def test_tiny_remainder_never_rounds_up_to_unaffordable_budget():
    assert daily_cap(usd(1), usd(0), date(2026, 11, 30)) == usd(0)


def test_mixed_currencies_raise():
    with pytest.raises(CurrencyMismatch):
        daily_cap(usd(100), Money(0, "EUR"), date(2026, 11, 1))


def test_worst_day_never_exceeds_remaining_on_a_grid():
    # Независимая проверка на рациональных числах: худший день = cap * (1 + 0,75).
    worst = Fraction(7, 4)
    for limit in (1, 99, 10_000, 123_457, 10**9):
        for spent in (0, 1, limit // 3, limit - 1):
            for day in (1, 7, 15, 29, 30):
                cap = daily_cap(usd(limit), usd(spent), date(2026, 11, day)).amount_minor
                assert cap * worst <= limit - spent
