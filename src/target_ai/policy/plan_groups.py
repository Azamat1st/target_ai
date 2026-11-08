"""Сборка и проверка одной группы плана: данные, интересы, размер аудитории, гео и возраст."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from target_ai.agents.schemas import ComplianceVerdict, SpecialCategory

from .categories import category_title_ru
from .plan_ads import validate_ads
from .plan_types import HYPOTHESES, HYPOTHESIS_RU, GroupInput, InterestInfo, PlanContext, ValidatedAd
from .rules import (
    MAX_INTERESTS_PER_GROUP, MIN_ADS_PER_GROUP, MIN_LOOKALIKE_SOURCE, MIN_RETARGETING_AUDIENCE, NARROW_AUDIENCE_SIZE,
    NARROW_AUDIENCE_SIZE_SMALL_RADIUS, SMALL_RADIUS_KM,
)

MIN_AGE = 18
MAX_AGE = 65
BROAD_AGE_HINT_MAX = 25
RESTRICTED_RADIUS_KM = 15
RESTRICTED_RADIUS_US_CA_KM = 25
US_CA = frozenset({"US", "CA"})


@dataclass(frozen=True)
class Candidate:
    label: str
    hypothesis: str
    share: Decimal
    interests: tuple[InterestInfo, ...]
    audience_size: int | None
    ads: tuple[ValidatedAd, ...]
    countries: tuple[str, ...]
    radius_km: int | None
    age_min: int
    age_max: int
    adult_only: bool = False


def label_for(hypothesis: str, index: int) -> str:
    return f"{HYPOTHESIS_RU.get(hypothesis, 'Группа')} №{index + 1}"


def _known(size: int | None) -> int | None:
    return size if size is not None and size >= 0 else None


def has_retargeting_data(ctx: PlanContext) -> bool:
    size = _known(ctx.engagement_audience_size)
    return size is not None and size >= MIN_RETARGETING_AUDIENCE


def _data_problem(hypothesis: str, ctx: PlanContext, category: SpecialCategory | None) -> str | None:
    if hypothesis == "retargeting" and not has_retargeting_data(ctx):
        return (f"недостаточно данных: аудитория вовлечённых в профиль меньше "
                f"{MIN_RETARGETING_AUDIENCE:,}".replace(",", " ") + " человек")
    if hypothesis == "lookalike":
        if category is not None:
            return "похожие аудитории недоступны для особых категорий"
        source = _known(ctx.lookalike_source_size)
        if source is None or source < MIN_LOOKALIKE_SOURCE or not has_retargeting_data(ctx):
            return "недостаточно данных: для похожих нужны источник от 1 000 человек и ретаргетинг"
    return None


def resolve_interests(
    keywords: tuple[str, ...], ctx: PlanContext
) -> tuple[tuple[InterestInfo, ...], list[str]]:
    """Интересы берутся только из каталога результатов поиска; остальное — в список ненайденных."""
    catalog = {key.strip().lower(): infos for key, infos in ctx.interest_catalog.items()}
    found: dict[str, InterestInfo] = {}
    missing: list[str] = []
    for keyword in keywords:
        infos = catalog.get(keyword.strip().lower())
        if not infos:
            missing.append(keyword)
        for info in infos or ():
            found.setdefault(info.id, info)
    return tuple(found.values())[:MAX_INTERESTS_PER_GROUP], missing


def union_estimate(interests: tuple[InterestInfo, ...]) -> int | None:
    """Оценка размера аудитории интересов: сумма известных (верхняя граница объединения)."""
    sizes = [s for s in (_known(i.audience_size) for i in interests) if s is not None]
    return sum(sizes) if sizes else None


def _lower_bound(interests: tuple[InterestInfo, ...]) -> int | None:
    sizes = [s for s in (_known(i.audience_size) for i in interests) if s is not None]
    return max(sizes) if sizes else None


def _audience_size(hypothesis: str, interests: tuple[InterestInfo, ...], ctx: PlanContext) -> int | None:
    if hypothesis == "broad":
        return _known(ctx.broad_audience_size)
    if hypothesis == "interests":
        return _lower_bound(interests)
    return _known(ctx.engagement_audience_size) if hypothesis == "retargeting" else None


def _narrow_problem(hypothesis: str, size: int | None, radius_km: int | None) -> str | None:
    if hypothesis not in ("broad", "interests") or size is None:
        return None  # «неизвестно» — не «пусто»
    small = radius_km is not None and radius_km <= SMALL_RADIUS_KM
    limit = NARROW_AUDIENCE_SIZE_SMALL_RADIUS if small else NARROW_AUDIENCE_SIZE
    if size < limit:
        return f"аудитория слишком узкая: оценка Meta {size:,} человек при пороге {limit:,}".replace(",", " ")
    return None


def _ages(group: GroupInput, category: SpecialCategory | None, adult: bool) -> tuple[int, int]:
    if category is not None:
        return MIN_AGE, MAX_AGE
    low = max(group.age_min if group.age_min is not None else MIN_AGE, MIN_AGE)
    if group.hypothesis == "broad":
        low = min(low, BROAD_AGE_HINT_MAX)
    high = min(group.age_max if group.age_max is not None else MAX_AGE, MAX_AGE)
    if adult:
        low = max(low, MIN_AGE)
    return (low, high) if low <= high else (MIN_AGE, MAX_AGE)


def _geo(
    group: GroupInput, ctx: PlanContext, category: SpecialCategory | None, label: str, notes: list[str]
) -> tuple[tuple[str, ...], int | None]:
    countries = group.countries or ctx.default_countries
    radius = group.radius_km
    if category is None or radius is None:
        return countries, radius
    needed = RESTRICTED_RADIUS_US_CA_KM if US_CA & set(countries) else RESTRICTED_RADIUS_KM
    if radius < needed:
        notes.append(f"{label}: радиус увеличен с {radius} до {needed} км — так требуют правила "
                     f"Meta для категории «{category_title_ru(category)}».")
        return countries, needed
    return countries, radius


def build_candidate(
    index: int, group: GroupInput, ctx: PlanContext, category: SpecialCategory | None, notes: list[str],
    verdict: ComplianceVerdict | None = None,
) -> Candidate | None:
    label = label_for(group.hypothesis, index)

    def drop(reason: str) -> None:
        notes.append(f"Группа «{label}» убрана: {reason}.")

    if group.hypothesis not in HYPOTHESES:
        return drop(f"неизвестный тип гипотезы «{group.hypothesis}»")
    if group.budget_share <= 0:
        return drop("доля бюджета должна быть положительным числом")
    problem = _data_problem(group.hypothesis, ctx, category)
    if problem:
        return drop(problem)
    interests: tuple[InterestInfo, ...] = ()
    if group.hypothesis == "interests":
        interests, missing = resolve_interests(group.interest_keywords, ctx)
        if missing:
            notes.append(f"{label}: интересов «{', '.join(missing)}» нет в результатах поиска Meta — "
                         "в таргетинг они не попадут.")
        if not interests:
            return drop("ни один интерес не найден поиском Meta")
    size = _audience_size(group.hypothesis, interests, ctx)
    problem = _narrow_problem(group.hypothesis, size, group.radius_km)
    if problem:
        return drop(problem)
    ads, adult = validate_ads(group.ads, ctx, label, notes, verdict)
    adult = adult or bool(verdict and verdict.adult_only)
    if len(ads) < MIN_ADS_PER_GROUP:
        return drop(f"осталось {len(ads)} допустимых объявлений, нужно не меньше {MIN_ADS_PER_GROUP}")
    if ctx.has_uploaded_media and not any(a.kind == "new_creative" for a in ads):
        return drop("есть присланные медиа, а в группе нет ни одного нового креатива")
    countries, radius = _geo(group, ctx, category, label, notes)
    low, high = _ages(group, category, adult)
    return Candidate(label, group.hypothesis, group.budget_share, interests, size, ads, countries,
                     radius, low, high, adult)
