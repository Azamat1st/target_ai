"""Паспорт компании в чате (R16, R17, R25): разделы, «не найдено», разбиение по ≤ 4096."""

from __future__ import annotations

import re
from decimal import Decimal

from target_ai.agents.schemas import NOT_FOUND, Passport
from target_ai.money import MoneyError, format_ru, from_major
from target_ai.policy import check_facts
from target_ai.storage import Fact

from .extract import OBJECTIVE_TITLES
from .texts import NOT_FOUND_RU, split_message

USER_SOURCE = "пользователь"
_DIGITS = re.compile(r"\d[\d\s.,]*\d|\d")


def _nf(value: str | None) -> str:
    text = (value or "").strip()
    return text if text and text != NOT_FOUND else NOT_FOUND_RU


def _list(items: list[str]) -> str:
    return "\n".join(f"• {i}" for i in items) if items else NOT_FOUND_RU


def _money(value: Decimal | None, currency: str | None) -> str:
    if value is None:
        return NOT_FOUND_RU
    try:
        return format_ru(from_major(value, currency)) if currency else str(value)
    except MoneyError:
        return str(value)


def offer_text(passport: Passport, fact_texts: list[str]) -> str:
    """Цены и числа без источника не печатаются: если их нет в фактах — числа скрыты."""
    offer = _nf(passport.offer)
    if offer != NOT_FOUND_RU and check_facts(offer, fact_texts):
        return f"{_DIGITS.sub('…', offer)} (цены и числа без источника скрыты)"
    return offer


def fact_line(fact: Fact) -> str:
    origin = "от вас" if fact.kind == "user" else f"источник: {fact.source}"
    return f"• {fact.text} — {origin}"


def render_passport(name: str, version: int, passport: Passport, facts: list[Fact], currency: str | None) -> list[str]:
    fact_texts = [f.text for f in facts]
    segments = [f"{s.name}\n  боли: {', '.join(s.pains) or NOT_FOUND_RU}\n  мотивы: {', '.join(s.motives) or NOT_FOUND_RU}"
                for s in passport.segments]
    competitors = [f"{c.name} ({_nf(c.url)}) — {_nf(c.difference)}" for c in passport.competitors]
    posts = [f"{p.media_id}: {p.why}" for p in passport.top_posts]
    voice = passport.voice
    blocks = [
        f"📒 Паспорт компании «{name}» (версия {version})",
        f"Что продаёт и почём:\n{offer_text(passport, fact_texts)}",
        f"Для кого:\n{_nf(passport.audience)}",
        f"УТП:\n{_nf(passport.usp)}",
        "Сегменты аудитории:\n" + "\n".join(segments),
        "Конкуренты:\n" + _list(competitors),
        "Какие посты заходят лучше:\n" + _list(posts),
        "Рекомендации (оценка Аналитика, это совет):\n"
        f"• Цель: {OBJECTIVE_TITLES[passport.recommended_objective]}\n"
        f"• Месячный бюджет: {_money(passport.recommended_monthly_budget, currency)} — {_nf(passport.budget_rationale)}\n"
        f"• Ожидаемая цена результата: {_money(passport.expected_cpa, currency)} — {_nf(passport.cpa_rationale)}",
        "Риски:\n" + _list(list(passport.risks)),
        f"Голос: язык {voice.language}, обращение на «{voice.address}», стиль: {_nf(voice.style)}",
        "Факты:\n" + _list([fact_line(f)[2:] for f in facts]).replace("\n• ", "\n• "),
    ]
    if passport.not_found:
        blocks.append("Не найдено: " + ", ".join(passport.not_found))
    return split_message(blocks)
