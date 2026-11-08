"""Проверка фактов (спека §3, R66): числа, цены, проценты, даты и слова-обещания из текста
должны встречаться в фактах компании. Детерминированно, без модели."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

_SPACES = str.maketrans({" ": " ", " ": " ", " ": " "})

_CURRENCY_WORDS = (
    r"\$|€|₽|₴|₸|₺|£|¥|руб(?:лей|ля|\.)?|р\.|грн|гривен\w*|долл\.?|доллар\w*|евро|"
    r"usd|eur|rub|uah|kzt|тенге|тг\b"
)
_CURRENCY_CANON = (
    ("₽", "RUB"), ("руб", "RUB"), ("р.", "RUB"), ("rub", "RUB"),
    ("$", "USD"), ("долл", "USD"), ("usd", "USD"),
    ("€", "EUR"), ("евро", "EUR"), ("eur", "EUR"),
    ("₴", "UAH"), ("грн", "UAH"), ("гривен", "UAH"), ("uah", "UAH"),
    ("₸", "KZT"), ("тенге", "KZT"), ("тг", "KZT"), ("kzt", "KZT"),
    ("£", "GBP"), ("₺", "TRY"), ("¥", "JPY"),
)
_DATE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})\b")
_AMOUNT = re.compile(
    rf"(?P<pre>{_CURRENCY_WORDS})?\s*(?P<num>\d{{1,3}}(?: \d{{3}})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    rf"\s*(?P<post>%|{_CURRENCY_WORDS})?",
    re.IGNORECASE,
)

# Слова-обещания: (шаблон). Обещание допустимо, только если тот же шаблон срабатывает в фактах.
_PROMISES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"гарант\w*", r"бесплатн\w*", r"скид(?:к|оч)\w*",
        r"лучш(?:ий|ая|ее|ие|его|ей|им|ими|их|ую|ему|ем)\b",
        r"(?:№|#|\bno\.?|\bномер)\s*(?:1|один)\b",
        r"\bfree\b", r"\bguarantee\w*", r"\bdiscount\w*", r"\bbest\b",
    )
)


@dataclass(frozen=True)
class _Token:
    kind: str  # "num" | "percent" | "date"
    value: str
    currency: str | None
    raw: str


def _normalize(text: str) -> str:
    return text.translate(_SPACES).replace("ё", "е").replace("Ё", "Е")


def _canon_currency(marker: str | None) -> str | None:
    if not marker:
        return None
    low = marker.lower().strip()
    return next((code for key, code in _CURRENCY_CANON if low.startswith(key)), None)


def _number_value(raw: str) -> str:
    return format(Decimal(raw.replace(" ", "").replace(",", ".")).normalize(), "f")


def _blank(text: str, spans: Iterable[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _tokens(text: str) -> list[_Token]:
    text = _normalize(text)
    found: list[_Token] = []
    for match in _DATE.finditer(text):
        day, month, year = match.groups()
        year = year if len(year) == 4 else f"20{year}"
        found.append(_Token("date", f"{int(day)}.{int(month)}.{year}", None, match.group(0)))
    text = _blank(text, (m.span() for m in _DATE.finditer(text)))
    for match in _AMOUNT.finditer(text):
        post = match.group("post")
        kind = "percent" if post == "%" else "num"
        marker = match.group("pre") or (None if post == "%" else post)
        raw = " ".join(match.group(0).split())
        found.append(_Token(kind, _number_value(match.group("num")), _canon_currency(marker), raw))
    return found


def _is_supported(token: _Token, facts: list[_Token]) -> str | None:
    """None — подтверждено фактами; иначе — причина."""
    same = [f for f in facts if f.value == token.value]
    if token.kind == "date":
        return None if any(f.kind == "date" for f in same) else "нет в фактах компании"
    if token.kind == "percent":
        return None if any(f.kind == "percent" for f in same) else "нет в фактах компании"
    if not same:
        return "нет в фактах компании"
    if token.currency and not any(f.currency in (None, token.currency) for f in same):
        return "в фактах эта сумма в другой валюте"
    return None


def check_facts(text: str, facts: Iterable[object]) -> list[str]:
    """Список нарушений на русском; пустой — в тексте нет ничего, чего нет в фактах."""
    blob = _normalize(" ".join(str(getattr(f, "text", f)) for f in facts))
    fact_tokens = _tokens(blob)
    body = _normalize(text)
    violations: list[str] = []
    seen: set[str] = set()
    spans: list[tuple[int, int]] = []
    for pattern in _PROMISES:
        for match in pattern.finditer(body):
            spans.append(match.span())
            key = f"word:{match.group(0).lower()}"
            if key not in seen and not pattern.search(blob):
                seen.add(key)
                violations.append(f"«{match.group(0)}»: слово-обещание, в фактах компании его нет.")
    for token in _tokens(_blank(body, spans)):
        reason = _is_supported(token, fact_tokens)
        key = f"{token.kind}:{token.value}:{token.currency}"
        if reason and key not in seen:
            seen.add(key)
            violations.append(f"«{token.raw}»: {reason}.")
    return violations
