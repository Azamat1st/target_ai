"""Входы и выходы проверки медиаплана (спека §3 «Валидация медиаплана», §5 «MediaPlanProposal»).

`PlanProposalInput` повторяет поля схемы Стратега по именам; адаптер в `services` тривиален.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from target_ai.money import Money

from target_ai.agents.schemas import Hypothesis, Objective, SpecialCategory
from .types import GoalKind

OBJECTIVES = tuple(o.value for o in Objective)  # имена целей — из agents.schemas
HYPOTHESES = tuple(h.value for h in Hypothesis)
AD_KINDS = ("existing_post", "new_creative")
HYPOTHESIS_RU = {
    "broad": "Широкая", "interests": "Интересы", "retargeting": "Ретаргетинг", "lookalike": "Похожие",
}


@dataclass(frozen=True)
class AdInput:
    kind: str  # existing_post | new_creative
    text: str
    creative_id: str
    headline: str = ""


@dataclass(frozen=True)
class GroupInput:
    hypothesis: str  # broad | interests | retargeting | lookalike
    budget_share: Decimal
    interest_keywords: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    radius_km: int | None = None
    age_min: int | None = None
    age_max: int | None = None
    ads: tuple[AdInput, ...] = ()
    name: str = ""


@dataclass(frozen=True)
class PlanProposalInput:
    objective: str
    groups: tuple[GroupInput, ...] = ()


@dataclass(frozen=True)
class InterestInfo:
    id: str  # только из результатов поиска Meta
    name: str
    audience_size: int | None = None  # None или -1 — «неизвестно», не «пусто»


@dataclass(frozen=True, kw_only=True)
class PlanContext:
    """Всё, что код знает о компании и аккаунте; модель этих значений не задаёт."""

    currency: str
    daily_cap: Money
    min_budget_imp: Money
    min_budget_high_freq: Money
    active_daily_total: Money | None = None  # уже работающие группы компании
    interest_catalog: Mapping[str, tuple[InterestInfo, ...]]
    engagement_audience_size: int | None = None
    lookalike_source_size: int | None = None
    broad_audience_size: int | None = None
    facts: tuple[str, ...] = ()
    creative_ids: frozenset[str] = frozenset()
    has_uploaded_media: bool = False
    language: str | None = None
    special_category: SpecialCategory | None = None
    company_texts: tuple[str, ...] = ()
    default_countries: tuple[str, ...] = ()
    fallback_objective: str | None = None
    demo: bool = False  # демо-уровень: вердикт needs_review не отбрасывает объявление


@dataclass(frozen=True)
class ValidatedAd:
    kind: str
    text: str
    creative_id: str
    headline: str = ""


@dataclass(frozen=True)
class ValidatedGroup:
    hypothesis: str
    daily_budget: Money
    goal_kind: GoalKind
    advantage_audience: Literal[0, 1]
    interests: tuple[InterestInfo, ...]
    exclude_retargeting: bool
    countries: tuple[str, ...]
    radius_km: int | None
    age_min: int
    age_max: int
    audience_size: int | None
    ads: tuple[ValidatedAd, ...]
    adult_only: bool = False


@dataclass(frozen=True)
class ValidatedPlan:
    objective: str | None
    groups: tuple[ValidatedGroup, ...]
    total_daily: Money
    special_category: SpecialCategory | None
    notes_ru: tuple[str, ...]  # что отброшено или изменено и почему
    blockers_ru: tuple[str, ...]  # почему план нельзя запускать

    @property
    def ok(self) -> bool:
        return bool(self.groups) and not self.blockers_ru


class PolicyInvariantError(RuntimeError):
    """Нарушен денежный инвариант внутри политики: такого быть не должно."""
