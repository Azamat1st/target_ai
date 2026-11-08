"""Таблица валют Meta: множитель минимальной единицы (offset).

Источник: https://developers.facebook.com/docs/marketing-api/currencies
(66 строк, сверено 2026-11-08). Валют вне этой страницы здесь нет намеренно.
"""

from .errors import UnknownCurrency

# Offset = 1: дробной части нет.
ZERO_DECIMAL_CURRENCIES = frozenset(
    {"CLP", "COP", "CRC", "HUF", "ISK", "IDR", "JPY", "KRW", "PYG", "TWD", "VND"}
)

# Offset = 100.
HUNDRED_CURRENCIES = frozenset(
    {
        "DZD", "ARS", "AUD", "BHD", "BDT", "BOB", "BGN", "BRL", "GBP", "CAD", "CNY", "HRK",
        "CZK", "DKK", "EGP", "EUR", "GTQ", "HNL", "HKD", "INR", "ILS", "JOD", "KES", "LVL",
        "LTL", "MOP", "MYR", "MXN", "NZD", "NIO", "NGN", "NOK", "PKR", "PEN", "PHP", "PLN",
        "QAR", "RON", "RUB", "SAR", "RSD", "SGD", "SKK", "ZAR", "SEK", "CHF", "THB", "TRY",
        "AED", "UAH", "USD", "UYU", "VEF", "FBZ", "VES",
    }
)

CURRENCY_SYMBOLS = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "RUB": "₽", "UAH": "₴", "TRY": "₺",
}


def offset(currency: str) -> int:
    """Сколько минимальных единиц в одной основной. Неизвестная валюта — ошибка."""
    if currency in ZERO_DECIMAL_CURRENCIES:
        return 1
    if currency in HUNDRED_CURRENCIES:
        return 100
    raise UnknownCurrency(f"Валюта «{currency}» не поддерживается рекламой Meta.")


def decimals(currency: str) -> int:
    """Число знаков после запятой в основной единице."""
    return 0 if offset(currency) == 1 else 2
