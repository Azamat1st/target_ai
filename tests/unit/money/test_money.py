from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from target_ai.money import (
    CurrencyMismatch,
    FractionalAmountError,
    Money,
    UnknownCurrency,
    format_ru,
    from_major,
    offset,
)


@pytest.mark.parametrize("cur", ["CLP", "COP", "CRC", "HUF", "ISK", "IDR", "JPY", "KRW", "PYG", "TWD", "VND"])
def test_offset_is_one_for_zero_decimal_currencies(cur):
    assert offset(cur) == 1


@pytest.mark.parametrize("cur", ["USD", "EUR", "GBP", "RUB"])
def test_offset_is_hundred_for_regular_currencies(cur):
    assert offset(cur) == 100


@pytest.mark.parametrize("cur", ["KZT", "UZS", "XXX", ""])
def test_unknown_currency_raises(cur):
    with pytest.raises(UnknownCurrency):
        offset(cur)


def test_money_is_immutable():
    m = Money(100, "USD")
    with pytest.raises(FrozenInstanceError):
        m.amount_minor = 5  # type: ignore[misc]


def test_money_rejects_float_and_unknown_currency():
    with pytest.raises(TypeError):
        Money(1.5, "USD")  # type: ignore[arg-type]
    with pytest.raises(UnknownCurrency):
        Money(100, "KZT")


def test_from_major_converts_to_minor_units():
    assert from_major(Decimal("12.34"), "USD").amount_minor == 1234
    assert from_major(Decimal("1500"), "JPY").amount_minor == 1500


def test_from_major_rejects_fraction_for_zero_decimal_currency():
    with pytest.raises(FractionalAmountError):
        from_major(Decimal("1500.5"), "JPY")


def test_from_major_rejects_sub_cent_amount():
    with pytest.raises(FractionalAmountError):
        from_major(Decimal("1.234"), "USD")


def test_from_major_rejects_float():
    with pytest.raises(TypeError):
        from_major(12.34, "USD")  # type: ignore[arg-type]


def test_to_major_round_trips():
    assert Money(1234, "USD").to_major() == Decimal("12.34")
    assert Money(1500, "JPY").to_major() == Decimal("1500")


def test_addition_and_comparison_in_same_currency():
    assert Money(100, "USD") + Money(250, "USD") == Money(350, "USD")
    assert Money(100, "USD") < Money(250, "USD")
    assert Money(300, "USD") - Money(100, "USD") == Money(200, "USD")


def test_mixed_currencies_raise():
    with pytest.raises(CurrencyMismatch):
        Money(100, "USD") + Money(100, "EUR")
    with pytest.raises(CurrencyMismatch):
        Money(100, "USD") < Money(100, "EUR")


def test_equality_across_currencies_is_false_not_error():
    assert Money(100, "USD") != Money(100, "EUR")


@pytest.mark.parametrize(
    "money,expected",
    [
        (Money(123450, "USD"), "1 234,50 $"),
        (Money(1500, "JPY"), "1 500 ¥"),
        (Money(1200, "EUR"), "12,00 €"),
        (Money(5, "USD"), "0,05 $"),
    ],
)
def test_format_ru(money, expected):
    assert format_ru(money) == expected
