"""Особые категории рекламы (спека §3, R67): классификация по ключевым словам."""

from __future__ import annotations

import re
from collections.abc import Iterable

from target_ai.agents.schemas import SpecialCategory


_FORBIDDEN = frozenset(
    {SpecialCategory.ISSUES_ELECTIONS_POLITICS, SpecialCategory.ONLINE_GAMBLING_AND_GAMING}
)
_TITLES_RU = {
    SpecialCategory.FINANCIAL_PRODUCTS_SERVICES: "финансовые продукты и услуги",
    SpecialCategory.HOUSING: "жильё",
    SpecialCategory.EMPLOYMENT: "работа",
    SpecialCategory.ISSUES_ELECTIONS_POLITICS: "политика и выборы",
    SpecialCategory.ONLINE_GAMBLING_AND_GAMING: "азартные игры",
}


def is_forbidden(category: SpecialCategory) -> bool:
    """Политика и азартные игры: запуск запрещён (нужна авторизация рекламодателя в Meta)."""
    return category in _FORBIDDEN


def category_title_ru(category: SpecialCategory) -> str:
    return _TITLES_RU[category]


# Порядок = строгость: сначала запрещённые, затем ограниченные.
_PATTERNS: tuple[tuple[SpecialCategory, re.Pattern[str]], ...] = tuple(
    (category, re.compile(pattern, re.IGNORECASE))
    for category, pattern in (
        (SpecialCategory.ONLINE_GAMBLING_AND_GAMING,
         r"казино|ставки на спорт|букмекер|покер|рулетк|игров\w+ автомат|лотере|тотализатор|casino"),
        (SpecialCategory.ISSUES_ELECTIONS_POLITICS,
         r"\bвыборы\b|\bвыборов\b|\bвыборах\b|предвыборн|референдум|депутат|избирател|"
         r"политическ\w+ парти|голосуйте за"),
        (SpecialCategory.FINANCIAL_PRODUCTS_SERVICES,
         r"кредит|ипотек|займ|заём|ссуд|рефинансир|овердрафт|страхов|микрофинанс"),
        (SpecialCategory.HOUSING,
         r"недвижимост|аренд\w* (?:квартир|жиль|комнат)|снять квартир|новостро|застройщик|"
         r"риелтор|риэлтор|жилой комплекс|купить квартир"),
        (SpecialCategory.EMPLOYMENT,
         r"вакан|трудоустр|ищем сотрудник|требуются|набираем (?:сотрудник|команду|персонал)|"
         r"подработк|\bработ[уы] в\b|\bработа в\b|удал[её]нн\w+ работ|предлагаем работу"),
    )
)


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е").replace("заем", "заём")


def classify_special_category(texts: Iterable[str]) -> SpecialCategory | None:
    """Самая строгая категория, найденная по ключевым словам в любом из текстов."""
    joined = _normalize(" \n ".join(texts))
    for category, pattern in _PATTERNS:
        if pattern.search(joined):
            return category
    return None


_STRICTNESS = (
    SpecialCategory.ONLINE_GAMBLING_AND_GAMING, SpecialCategory.ISSUES_ELECTIONS_POLITICS,
    SpecialCategory.FINANCIAL_PRODUCTS_SERVICES, SpecialCategory.HOUSING, SpecialCategory.EMPLOYMENT,
)


def stricter_category(
    first: SpecialCategory | None, second: SpecialCategory | None
) -> SpecialCategory | None:
    """Из двух оценок (Контролёра и кода) берётся более строгая."""
    found = [c for c in (first, second) if c is not None]
    return min(found, key=_STRICTNESS.index) if found else None
