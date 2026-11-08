"""Разбор недоверенного словаря в PlanProposalInput: всё вне схемы отбрасывается с пояснением."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from target_ai.agents.schemas import AdCopySet, MediaPlanProposal

from .plan_types import AdInput, GroupInput, PlanProposalInput

_TOP = {"objective", "groups", "objective_rationale", "notes"}
_GROUP = {"hypothesis", "budget_share", "interest_keywords", "countries", "radius_km",
          "age_min", "age_max", "ads", "name", "rationale", "locations"}
_AD = {"kind", "text", "creative_id", "headline", "primary_text", "cta"}


def _extras(raw: Mapping[str, Any], allowed: set[str], notes: list[str]) -> None:
    for key in raw:
        if key not in allowed:
            notes.append(f"Поле «{key}» не предусмотрено схемой плана и отброшено: деньги, "
                         "ID объектов, площадки и лимиты задаёт только код.")


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _texts(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _share(value: Any) -> Decimal:
    if isinstance(value, bool):
        return Decimal(0)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return number if number.is_finite() else Decimal(0)


def _ad(raw: Any, notes: list[str]) -> AdInput | None:
    if not isinstance(raw, Mapping):
        notes.append("Объявление не в формате схемы и отброшено.")
        return None
    _extras(raw, _AD, notes)
    text = _text(raw.get("text")) or _text(raw.get("primary_text"))
    return AdInput(_text(raw.get("kind")), text, _text(raw.get("creative_id")), _text(raw.get("headline")))


def _group(raw: Any, notes: list[str]) -> GroupInput | None:
    if not isinstance(raw, Mapping):
        notes.append("Группа не в формате схемы и отброшена.")
        return None
    _extras(raw, _GROUP, notes)
    ads_raw = raw.get("ads")
    ads = [a for a in (_ad(x, notes) for x in ads_raw) if a] if isinstance(ads_raw, list | tuple) else []
    return GroupInput(
        hypothesis=_text(raw.get("hypothesis")), budget_share=_share(raw.get("budget_share")),
        name=_text(raw.get("name")), interest_keywords=_texts(raw.get("interest_keywords")),
        countries=_texts(raw.get("countries")) or _texts(raw.get("locations")),
        radius_km=_int(raw.get("radius_km")), age_min=_int(raw.get("age_min")),
        age_max=_int(raw.get("age_max")), ads=tuple(ads),
    )


def coerce_proposal(raw: Mapping[str, Any]) -> tuple[PlanProposalInput, list[str]]:
    """Словарь из ответа модели → входной протокол; вторым значением — пояснения об отброшенном."""
    notes: list[str] = []
    _extras(raw, _TOP, notes)
    groups_raw = raw.get("groups")
    items = groups_raw if isinstance(groups_raw, list | tuple) else []
    groups = tuple(g for g in (_group(x, notes) for x in items) if g)
    return PlanProposalInput(_text(raw.get("objective")), groups), list(dict.fromkeys(notes))


def from_schema(plan: MediaPlanProposal, copy: Sequence[AdCopySet | None] | None) -> PlanProposalInput:
    """Предложение Стратега и тексты Копирайтера по группам → входной протокол без переименований."""
    groups = []
    for index, group in enumerate(plan.groups):
        copy_set = copy[index] if copy and index < len(copy) else None
        ads = tuple(
            AdInput(v.kind, v.primary_text, v.creative_id or "", v.headline)
            for v in (copy_set.variants if copy_set else ())
        )
        groups.append(GroupInput(
            hypothesis=group.hypothesis.value, budget_share=group.budget_share, name=group.name,
            interest_keywords=group.interest_keywords, countries=group.locations,
            age_min=group.age_min, age_max=group.age_max, ads=ads))
    return PlanProposalInput(plan.objective.value, tuple(groups))
