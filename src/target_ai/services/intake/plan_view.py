"""Короткое сообщение медиаплана (≤ 1 экран, R24)."""

from __future__ import annotations

from collections.abc import Sequence

from target_ai.agents.schemas import Objective
from target_ai.money import Money, format_ru
from target_ai.policy import ValidatedPlan
from target_ai.policy.plan_types import HYPOTHESIS_RU

from .extract import OBJECTIVE_TITLES

MAX_NOTES = 4
NOTE_LIMIT = 170
HISTORY_LINES = 3


def _clip(text: str, limit: int = NOTE_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_plan(*, company_name: str, version: int, validated: ValidatedPlan, extra_notes: Sequence[str],
                history: Sequence[str], monthly_limit: Money | None, expected_cpa: str | None,
                spend_cap_warning: str | None, objective_note: str | None) -> str:
    objective = Objective(validated.objective) if validated.objective else None
    lines = [f"📋 Медиаплан v{version} — «{company_name}»"]
    if objective:
        lines.append(f"Цель: {OBJECTIVE_TITLES[objective]}")
    if validated.groups:
        limit = f" (месячный лимит {format_ru(monthly_limit)})" if monthly_limit else ""
        lines.append(f"Бюджет: {format_ru(validated.total_daily)} в день{limit}")
        lines.append("Группы:")
        for n, group in enumerate(validated.groups, 1):
            size = f", аудитория ≈ {group.audience_size:,}".replace(",", " ") if group.audience_size else ""
            lines.append(f"{n}. {HYPOTHESIS_RU.get(group.hypothesis, group.hypothesis)} — "
                         f"{format_ru(group.daily_budget)}/день, объявлений: {len(group.ads)}{size}")
    if expected_cpa:
        lines.append(f"Целевая цена: {expected_cpa}")
    if history:
        lines.append("Учтено из прошлого: " + "; ".join(_clip(h, 80) for h in history[:HISTORY_LINES]))
    warnings = [*([objective_note] if objective_note else []), *([spend_cap_warning] if spend_cap_warning else []),
                *validated.blockers_ru, *validated.notes_ru, *extra_notes]
    for warning in warnings[:MAX_NOTES + 2]:
        lines.append(f"⚠️ {_clip(warning)}")
    if len(warnings) > MAX_NOTES + 2:
        lines.append(f"…и ещё замечаний: {len(warnings) - MAX_NOTES - 2}")
    return "\n".join(lines)
