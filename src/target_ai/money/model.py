"""Тип денег: целые минимальные единицы + валюта."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .currencies import CURRENCY_SYMBOLS, decimals, offset
from .errors import CurrencyMismatch, FractionalAmountError


@dataclass(frozen=True)
class Money:
    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise TypeError("amount_minor должен быть целым числом")
        offset(self.currency)  # валидирует валюту

    def _same(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError("Сравнивать и складывать можно только Money")
        if other.currency != self.currency:
            raise CurrencyMismatch(f"Нельзя смешивать {self.currency} и {other.currency}.")

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._same(other)
        return self.amount_minor < other.amount_minor

    def __le__(self, other: Money) -> bool:
        self._same(other)
        return self.amount_minor <= other.amount_minor

    def __gt__(self, other: Money) -> bool:
        self._same(other)
        return self.amount_minor > other.amount_minor

    def __ge__(self, other: Money) -> bool:
        self._same(other)
        return self.amount_minor >= other.amount_minor

    def to_major(self) -> Decimal:
        return Decimal(self.amount_minor) / Decimal(offset(self.currency))


def from_major(value: Decimal, currency: str) -> Money:
    """Единственное место пересчёта основной единицы в минимальную."""
    if not isinstance(value, Decimal):
        raise TypeError("Сумма должна быть Decimal, не float")
    scaled = value * offset(currency)
    if scaled != scaled.to_integral_value():
        raise FractionalAmountError(f"Сумма {value} {currency} не делится на минимальную единицу.")
    return Money(int(scaled), currency)


def format_ru(money: Money) -> str:
    """«1 234,50 $» — пробел-разделитель тысяч, запятая-дробь."""
    places = decimals(money.currency)
    text = f"{money.to_major():,.{places}f}".replace(",", " ").replace(".", ",")
    return f"{text} {CURRENCY_SYMBOLS.get(money.currency, money.currency)}"
