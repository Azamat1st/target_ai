"""Исключения модуля денег."""


class MoneyError(ValueError):
    """Базовая ошибка денежных операций."""


class UnknownCurrency(MoneyError):
    """Валюты нет в таблице Meta: не угадываем, а отказываем."""


class FractionalAmountError(MoneyError):
    """Сумма не делится на минимальную единицу валюты."""


class CurrencyMismatch(MoneyError):
    """Операция над суммами в разных валютах."""
