"""Валидация медиаплана (спека §3): модель предлагает, код решает и считает деньги."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import Any

from target_ai.agents.schemas import (
    AdCopySet, ComplianceVerdict, MediaPlanProposal, Objective, SpecialCategory,
)
from target_ai.money import CurrencyMismatch, Money, format_ru

from .categories import (
    category_title_ru, classify_special_category, is_forbidden, stricter_category,
)
from .plan_groups import (
    Candidate, build_candidate, has_retargeting_data, label_for, union_estimate,
)
from .plan_types import (
    OBJECTIVES, PlanContext, PlanProposalInput, PolicyInvariantError, ValidatedGroup, ValidatedPlan,
)
from .proposal import coerce_proposal, from_schema
from .rules import MAX_GROUPS, MIN_GROUPS
from .types import GoalKind

INSTAGRAM_POSITIONS = {
    Objective.DIRECT_MESSAGES.value: ("stream", "story"),
    Objective.LEAD_FORM.value: ("stream", "story"),
    Objective.WEBSITE_TRAFFIC.value: ("stream", "story", "reels"),
    Objective.PROFILE_TRAFFIC.value: ("stream", "story", "reels"),
    Objective.REACH.value: ("stream", "story", "reels"),
}


def goal_kind_for(objective: str) -> GoalKind:
    return "imp" if objective == Objective.REACH.value else "high_freq"


def _texts_of(proposal: PlanProposalInput, ctx: PlanContext) -> list[str]:
    found = list(ctx.company_texts)
    for group in proposal.groups:
        found.extend(group.interest_keywords)
        for ad in group.ads:
            found.extend((ad.text, ad.headline))
    return found


def _blocked(
    objective: str | None, category: SpecialCategory | None, notes: list[str], reason: str, ctx: PlanContext
) -> ValidatedPlan:
    return ValidatedPlan(objective, (), Money(0, ctx.currency), category, tuple(notes), (reason,))


def _forbidden_reason(category: SpecialCategory) -> str:
    return (f"Категория «{category_title_ru(category)}»: запуск запрещён — для такой рекламы нужна отдельная "
            "авторизация рекламодателя в Meta. Категорию можно поправить через «Изменить».")


def _resolve_objective(proposal: PlanProposalInput, ctx: PlanContext, notes: list[str]) -> str | None:
    if proposal.objective in OBJECTIVES:
        return proposal.objective
    if ctx.fallback_objective in OBJECTIVES:
        notes.append(f"Цель «{proposal.objective}» не поддерживается, взята «{ctx.fallback_objective}».")
        return ctx.fallback_objective
    return None


def _drop_overlapping(candidates: list[Candidate], notes: list[str]) -> list[Candidate]:
    """Интерес-группы с ≥50% общих интересов или подмножеством: остаётся с большей оценкой."""
    dropped: set[int] = set()
    for i, first in enumerate(candidates):
        for j in range(i + 1, len(candidates)):
            second = candidates[j]
            both_interests = first.hypothesis == second.hypothesis == "interests"
            if i in dropped or j in dropped or not both_interests:
                continue
            a, b = {x.id for x in first.interests}, {x.id for x in second.interests}
            if len(a & b) * 2 < min(len(a), len(b)):
                continue
            keep_first = (union_estimate(first.interests) or 0) >= (union_estimate(second.interests) or 0)
            loser, winner = (second, first) if keep_first else (first, second)
            dropped.add(j if keep_first else i)
            notes.append(f"Группа «{loser.label}» убрана: интересы пересекаются с «{winner.label}» "
                         "(≥50% общих), оставлена аудитория с большей оценкой.")
    return [c for k, c in enumerate(candidates) if k not in dropped]


def _budgets(candidates: list[Candidate], available: int) -> list[int]:
    total = sum(Fraction(c.share) for c in candidates)
    amounts = [int(Fraction(available) * Fraction(c.share) / total) for c in candidates]
    biggest = max(range(len(candidates)), key=lambda k: candidates[k].share)
    amounts[biggest] += available - sum(amounts)  # остаток от округления вниз — самой крупной
    return amounts


def _fit_to_budget(
    candidates: list[Candidate], available: int, ctx: PlanContext, objective: str, notes: list[str]
) -> tuple[list[Candidate], list[int]]:
    minimum = (ctx.min_budget_imp if goal_kind_for(objective) == "imp" else ctx.min_budget_high_freq)
    while candidates:
        amounts = _budgets(candidates, available)
        if all(a >= minimum.amount_minor for a in amounts):
            return candidates, amounts
        weakest = min(range(len(candidates)), key=lambda k: (candidates[k].share, -k))
        dropped = candidates[weakest]
        remaining = [c for k, c in enumerate(candidates) if k != weakest]
        notes.append(
            f"Группа «{dropped.label}» убрана: её доля дала бы меньше, чем минимум Meta "
            f"{format_ru(minimum)} в день; бюджета {format_ru(Money(available, ctx.currency))} в день "
            f"хватает на {len(remaining)} гр.")
        candidates = remaining
    return [], []


def _finish_group(c: Candidate, amount: int, objective: str, exclude: bool, ctx: PlanContext) -> ValidatedGroup:
    return ValidatedGroup(
        hypothesis=c.hypothesis, daily_budget=Money(amount, ctx.currency),
        goal_kind=goal_kind_for(objective), advantage_audience=1 if c.hypothesis == "broad" else 0,
        interests=c.interests, exclude_retargeting=exclude and c.hypothesis != "retargeting", adult_only=c.adult_only,
        countries=c.countries, radius_km=c.radius_km, age_min=c.age_min, age_max=c.age_max,
        audience_size=c.audience_size, ads=c.ads,
    )


def _prepare(
    proposal: PlanProposalInput, ctx: PlanContext, category: SpecialCategory | None, notes: list[str],
    verdicts: Sequence[ComplianceVerdict | None],
) -> list[Candidate]:
    built = (
        build_candidate(i, g, ctx, category, notes, verdicts[i] if i < len(verdicts) else None)
        for i, g in enumerate(proposal.groups)
    )
    candidates = _drop_overlapping([c for c in built if c is not None], notes)
    if len(candidates) > MAX_GROUPS:
        notes.append(f"Групп больше {MAX_GROUPS}: оставлены первые {MAX_GROUPS}, остальные убраны.")
        candidates = candidates[:MAX_GROUPS]
    return candidates


def _check_currencies(ctx: PlanContext) -> None:
    monies = [ctx.daily_cap, ctx.min_budget_imp, ctx.min_budget_high_freq]
    if ctx.active_daily_total is not None:
        monies.append(ctx.active_daily_total)
    for money in monies:
        if money.currency != ctx.currency:
            raise CurrencyMismatch(f"Контекст плана в {ctx.currency}, а сумма в {money.currency}.")


def validate_plan(
    proposal: PlanProposalInput | MediaPlanProposal | Mapping[str, Any],
    ctx: PlanContext,
    *,
    copy: Sequence[AdCopySet | None] | None = None,
    verdicts: Sequence[ComplianceVerdict | None] | None = None,
) -> ValidatedPlan:
    """Итоговый план всегда в лимите и в правилах; о каждом отброшенном — строка-причина.

    `copy[i]` — тексты группы i, `verdicts[i]` — вердикт Контролёра по ней; без `verdicts`
    Контролёр не опрашивался и действуют только детерминированные проверки.
    """
    _check_currencies(ctx)
    notes: list[str] = []
    if isinstance(proposal, MediaPlanProposal):
        proposal = from_schema(proposal, copy)
    elif isinstance(proposal, Mapping):
        proposal, extra = coerce_proposal(proposal)
        notes.extend(extra)
    verdict_list = list(verdicts or ())
    category = stricter_category(ctx.special_category, classify_special_category(_texts_of(proposal, ctx)))
    for verdict in verdict_list:
        category = stricter_category(category, verdict.category if verdict else None)
    objective = _resolve_objective(proposal, ctx, notes)
    if category is not None and is_forbidden(category):
        return _blocked(objective, category, notes, _forbidden_reason(category), ctx)
    if objective is None:
        return _blocked(None, category, notes, "Цель плана не из поддерживаемых.", ctx)
    taken = ctx.active_daily_total.amount_minor if ctx.active_daily_total else 0
    available = max(0, ctx.daily_cap.amount_minor - taken)
    candidates, amounts = _fit_to_budget(_prepare(proposal, ctx, category, notes, verdict_list), available, ctx, objective, notes)
    if not candidates:
        reason = (f"В плане не осталось групп: дневной бюджет {format_ru(Money(available, ctx.currency))} "
                  "не покрывает минимум Meta или предложение не прошло проверки.")
        return _blocked(objective, category, notes, reason, ctx)
    if category is not None and any(not c.countries for c in candidates):
        return _blocked(objective, category, notes,
                        "Для особой категории нужна страна показа (special_ad_category_country).", ctx)
    if len(candidates) < MIN_GROUPS:
        notes.append("В плане меньше двух групп: так вышло после проверок и бюджета.")
    exclude = has_retargeting_data(ctx)
    groups = tuple(_finish_group(c, a, objective, exclude, ctx) for c, a in zip(candidates, amounts, strict=True))
    total = Money(sum(amounts), ctx.currency)
    if total.amount_minor > available:
        raise PolicyInvariantError("Сумма бюджетов плана превысила доступный потолок.")
    return ValidatedPlan(objective, groups, total, category, tuple(dict.fromkeys(notes)), ())


__all__ = ["INSTAGRAM_POSITIONS", "goal_kind_for", "label_for", "validate_plan"]
