"""Разбор суммы из русского текста по строгой грамматике.

Однозначное разбирается; неоднозначное — ValueError с подсказкой (бот переспросит);
названная валюта вне таблицы Meta — UnknownCurrency. Догадок нет.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from .currencies import HUNDRED_CURRENCIES, ZERO_DECIMAL_CURRENCIES
from .errors import UnknownCurrency

_SP = "  "
_HINT_THOUSANDS = "Напишите сумму без точек и запятых в тысячах, например 1500 или 1 500"
_HINT_SEVERAL = "Нашёл несколько сумм — напишите одну"
_HINT_UNKNOWN_CURRENCY = (
    "Meta не поддерживает эту валюту — укажите сумму в валюте рекламного аккаунта"
)

# Целая часть: простое число или группы по ровно 3 цифры через пробел; дробь — 1–2 знака.
_NUMBER = re.compile(
    rf"(?<![\d.,])(\d{{1,3}}(?:[{_SP}]\d{{3}})+|\d+)(?:[.,](\d+))?(?!\d)"
)
_TOKEN_AFTER = re.compile(rf"[{_SP}]*(?:([a-zа-яё]+)\.?|([^\w\s]))", re.IGNORECASE)
# Токены фрагмента: «R$» и т.п. (буквы впритык к $) — один токен; любая другая буква — слово.
_ALL_TOKENS = re.compile(r"[^\W\d_]+\$|[^\W\d]+|[^\w\s]")
_MAX_LENGTH = 60
_HINT_TOO_LONG = "Напишите только сумму, например: 1500 € в месяц"
_HINT_SEVERAL_CURRENCIES = "Нашёл несколько валют — напишите одну"
_PUNCTUATION_EXTRA = frozenset("%~+")

_THOUSAND = {"тыс", "тысяч", "тысячи", "тысяча", "тысячу"}
_MILLION = {"млн", "миллион", "миллиона", "миллионов"}
_SHORT_K = {"к", "k"}  # только слитно с числом: через пробел «к» — предлог
_NOT_AMOUNT = {
    "дн", "дней", "день", "дня", "раз", "раза", "шт", "ч", "час", "часа", "часов", "%",
}

_SUPPORTED_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (code, re.compile(pattern))
    for code, pattern in (
        ("USD", r"\$|usd|долл|доллар(а|ов|ы|у|ах|ам|ами)?"),
        ("EUR", r"€|eur|евро"),
        ("RUB", r"₽|rub|руб(л(ь|я|ей|ю|ям|ях))?|р"),
        ("JPY", r"¥|jpy|[ий]ены?|[ий]ен"),
        ("TRY", r"₺|лир(а|ы|у|е)?"),
        ("GBP", r"£|фунт(а|ов|ы)?"),
        ("UAH", r"₴|грн|гривн[а-яё]*|гривен"),
        ("CNY", r"юан(ь|я|ей|и)"),
    )
)
_UNSUPPORTED = re.compile(
    r"₸|тенге|kzt|uzs|byn|kgs|mdl|azn|gel|amd|tjs|tmt|сум|сом|драм|лари|манат"
)
# Белый список: любое буквенное слово фрагмента — множитель, валюта или одно из этих.
_SERVICE_WORDS = frozenset(
    {
        "в", "на", "за", "до", "по", "с", "и", "а", "или", "к", "k", "то", "есть", "месяц",
        "мес", "день", "дн", "неделю", "ежемесячно", "ежедневно", "бюджет", "лимит", "рекламу",
        "примерно", "около", "максимум", "не", "больше", "меньше", "валюта",
        "дней", "дня", "раз", "раза", "шт", "ч", "час", "часа", "часов",
    }
) | _THOUSAND | _MILLION
_CODES = HUNDRED_CURRENCIES | ZERO_DECIMAL_CURRENCIES


def _parse_number(match: re.Match[str]) -> Decimal:
    fraction = match.group(2)
    if fraction is not None and len(fraction) > 2:
        raise ValueError(_HINT_THOUSANDS)
    integer = re.sub(rf"[{_SP}]", "", match.group(1))
    return Decimal(f"{integer}.{fraction}") if fraction else Decimal(integer)


def _after(text: str, pos: int) -> tuple[tuple[str, bool] | None, int]:
    found = _TOKEN_AFTER.match(text, pos)
    if not found:
        return None, pos
    word = (found.group(1) or found.group(2)).lower()
    glued = found.start(1) == pos or found.start(2) == pos
    return (word, glued), found.end()


def _multiplier(token: tuple[str, bool] | None) -> int | None:
    if token is None:
        return None
    word, glued = token
    if word in _THOUSAND:
        return 1_000
    if word in _MILLION:
        return 1_000_000
    if word in _SHORT_K and glued:
        return 1_000
    return None


def _currency_of(word: str) -> str | None:
    for code, pattern in _SUPPORTED_WORDS:
        if pattern.fullmatch(word):
            return code
    return word.upper() if word.upper() in _CODES else None


def _is_neutral(token: str) -> bool:
    """Служебное слово, знак препинания: сумму и валюту не задаёт."""
    if token in _SERVICE_WORDS:
        return True
    return len(token) == 1 and (
        unicodedata.category(token).startswith("P") or token in _PUNCTUATION_EXTRA
    )


def _classify_tokens(text: str) -> str | None:
    """Проверяет КАЖДЫЙ токен фрагмента; возвращает единственную валюту или None."""
    found: set[str] = set()
    for token in _ALL_TOKENS.findall(text.lower()):
        if _UNSUPPORTED.fullmatch(token) and token not in _SERVICE_WORDS:
            raise UnknownCurrency(_HINT_UNKNOWN_CURRENCY)
        if code := _currency_of(token):
            found.add(code)
        elif not _is_neutral(token):
            raise UnknownCurrency(
                f"Не понял „{token}“ — укажите сумму в валюте рекламного аккаунта"
            )
    if len(found) > 1:
        raise ValueError(_HINT_SEVERAL_CURRENCIES)
    return next(iter(found), None)


def parse_amount_ru(text: str) -> tuple[Decimal, str | None]:
    """Возвращает (сумма в основных единицах, код валюты или None).

    ValueError — нет суммы, фрагмент длиннее 60 символов, сумм/валют несколько или запись
    неоднозначна (с подсказкой для пользователя); UnknownCurrency — любое слово или символ
    фрагмента вне белого списка либо валюта вне таблицы Meta.
    """
    if len(text) > _MAX_LENGTH:
        raise ValueError(_HINT_TOO_LONG)
    amounts: set[Decimal] = set()
    for match in _NUMBER.finditer(text):
        value = _parse_number(match)
        following, _ = _after(text, match.end())
        if following and following[0] in _NOT_AMOUNT:
            continue
        factor = _multiplier(following)
        if factor is not None:
            value *= factor
        amounts.add(value)
    if not amounts:
        raise ValueError(f"В «{text}» не нашёл сумму — напишите, например, 500 или 1 500 $")
    if len(amounts) > 1:
        raise ValueError(_HINT_SEVERAL)
    return next(iter(amounts)), _classify_tokens(text)
