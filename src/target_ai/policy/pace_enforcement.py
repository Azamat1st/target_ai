"""Подгонка расхода под потолок дня (спека §3 «Темп»): снижение — автоматически."""

from __future__ import annotations

from target_ai.money import Money, format_ru

from .cpa import strength_key
from .optimizer import OptimizerDecision
from .types import CompanySnapshot, GroupState


def _pause(group: GroupState, reason: str) -> OptimizerDecision:
    return OptimizerDecision("pause_group", group.id, None, f"«{group.name or group.id}»: {reason}")


def _shrink(
    kept: list[GroupState], cap: int, snapshot: CompanySnapshot
) -> list[OptimizerDecision]:
    """Бюджеты ужимаются пропорционально запасу над минимумом Meta; сумма не выше потолка."""
    minimums = [snapshot.min_budget(g.goal_kind).amount_minor for g in kept]
    slack = [g.daily_budget.amount_minor - m for g, m in zip(kept, minimums, strict=True)]
    extra, total_slack = cap - sum(minimums), sum(slack)
    shown = format_ru(Money(cap, snapshot.currency))
    result = []
    for group, minimum, room in zip(kept, minimums, slack, strict=True):
        share = extra * room // total_slack if total_slack > 0 else 0
        new = min(group.daily_budget.amount_minor, minimum + share)
        if new < group.daily_budget.amount_minor:
            money = Money(new, snapshot.currency)
            result.append(OptimizerDecision(
                "set_budget", group.id, money,
                f"«{group.name or group.id}»: бюджет снижен с {format_ru(group.daily_budget)} до "
                f"{format_ru(money)} — потолок на сегодня {shown} (остаток лимита делится на "
                "оставшиеся дни).", lowers_for_limit=True))
    return result


def enforce_pace(snapshot: CompanySnapshot) -> list[OptimizerDecision]:
    """Решения, возвращающие сумму дневных бюджетов в потолок; пусто, если всё в пределах."""
    active = [g for g in snapshot.groups if snapshot.is_spending(g)]
    cap = snapshot.daily_cap.amount_minor
    if not active or snapshot.active_total <= cap and snapshot.remaining.amount_minor > 0:
        return []
    if snapshot.remaining.amount_minor == 0:
        return [_pause(g, "выключена — лимит месяца исчерпан, кампании компании на паузе.") for g in active]
    strongest_first = sorted(active, key=lambda g: strength_key(g.spend, g.results))
    kept = list(strongest_first)
    paused: list[GroupState] = []
    while kept and sum(snapshot.min_budget(g.goal_kind).amount_minor for g in kept) > cap:
        paused.append(kept.pop())
    shown = format_ru(Money(cap, snapshot.currency))
    if not kept:
        return [_pause(g, f"выключена — потолок на сегодня {shown} меньше минимума Meta для группы, "
                          "компания на паузе до конца месяца.") for g in active]
    reason = f"выключена — потолок на сегодня {shown} меньше суммы минимумов Meta, остаются сильнейшие."
    return [*(_pause(g, reason) for g in paused), *_shrink(kept, cap, snapshot)]
