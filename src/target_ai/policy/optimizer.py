"""Правила оптимизатора (спека §3, R54–R58): детерминированные решения по статистике."""

from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Literal

from target_ai.money import Money, format_ru

from .actions import Action, Pause, SetDailyBudget
from .cpa import cpa_at_least, cpa_at_most, cpa_money, spend_at_least, strength_key
from .check import action_key, check_in_batch
from .types import AdState, Allowed, CompanySnapshot, Decision, GroupState

Kind = Literal["pause_group", "pause_ad", "set_budget", "scale", "fatigue_warning"]


@dataclass(frozen=True)
class OptimizerDecision:
    kind: Kind
    target_id: str
    new_budget: Money | None
    reason_ru: str
    lowers_for_limit: bool = False

    def to_action(self) -> Action | None:
        if self.kind in ("pause_group", "pause_ad"):
            return Pause(self.target_id)
        if self.kind != "fatigue_warning" and self.new_budget is not None:
            return SetDailyBudget(self.target_id, self.new_budget, self.lowers_for_limit)
        return None  # предупреждение о выгорании — не действие


TRANSFER_TEXT = "бюджет переносится на сильные группы"
NO_STRONG_TEXT = "сильных групп пока нет — бюджет не перераспределялся"


def _comma(value) -> str:
    return f"{value:.1f}".replace(".", ",")


def _title(group: GroupState) -> str:
    return f"«{group.name or group.id}»"


def _days(group: GroupState, snapshot: CompanySnapshot) -> int:
    hours = (snapshot.now - group.active_since).total_seconds() / 3600 if group.active_since else 0
    return max(1, round(hours / 24))


def _is_mature(group: GroupState, snapshot: CompanySnapshot, target: Money) -> bool:
    rules = snapshot.rules
    if not snapshot.is_spending(group) or group.active_since is None:
        return False
    old_enough = (snapshot.now - group.active_since).total_seconds() >= rules.min_hours_before_decision * 3600
    return old_enough and spend_at_least(group.spend, target, rules.min_spend_target_cpa_multiple)


def _can_change_budget(group: GroupState, snapshot: CompanySnapshot) -> bool:
    last = group.last_budget_change_at
    hours = snapshot.rules.budget_change_min_interval_hours
    return last is None or (snapshot.now - last).total_seconds() >= hours * 3600


def _cpa_text(group: GroupState, target: Money, snapshot: CompanySnapshot) -> str:
    days = _days(group, snapshot)
    if group.results <= 0:
        return f"ни одного результата при расходе {format_ru(group.spend)} за {days} дн."
    return (f"цена результата {format_ru(cpa_money(group.spend, group.results))} "
            f"при целевой {format_ru(target)} за {days} дн.")


def _group_kills(mature: list[GroupState], target: Money, snapshot: CompanySnapshot) -> list[OptimizerDecision]:
    multiple = snapshot.rules.kill_cpa_multiple
    return [
        OptimizerDecision("pause_group", g.id, None,
                          f"{_title(g)}: выключена — {_cpa_text(g, target, snapshot)}.")
        for g in mature if cpa_at_least(g.spend, g.results, target, multiple)
    ]


def _is_weak_ad(ad: AdState, group: GroupState, target: Money, snapshot: CompanySnapshot) -> bool:
    rules = snapshot.rules
    if not ad.active or ad.spend is None or ad.impressions < rules.weak_ad_min_impressions:
        return False
    if ad.results <= 0:
        return ad.spend >= target
    limit = Fraction(rules.weak_ad_cpa_multiple) * Fraction(group.spend.amount_minor, group.results)
    return Fraction(ad.spend.amount_minor, ad.results) >= limit


def _ad_kills(group: GroupState, target: Money, snapshot: CompanySnapshot) -> list[OptimizerDecision]:
    active_ads = [a for a in group.ads if a.active]
    weak = [a for a in active_ads if _is_weak_ad(a, group, target, snapshot)]
    weak.sort(key=lambda a: strength_key(a.spend, a.results), reverse=True)
    average = format_ru(cpa_money(group.spend, group.results))
    result = []
    for ad in weak[: max(0, len(active_ads) - 1)]:  # хотя бы одно объявление остаётся
        price = (f"цена результата {format_ru(cpa_money(ad.spend, ad.results))}" if ad.results > 0
                 else f"ни одного результата при расходе {format_ru(ad.spend)}")
        result.append(OptimizerDecision(
            "pause_ad", ad.id, None,
            f"Объявление {ad.id} в {_title(group)}: выключено — {ad.impressions} показов, {price} "
            f"при средней по группе {average}."))
    return result


def _pct_of(minor: int, pct: int) -> int:
    return minor * pct // 100


def _budget_move(group: GroupState, new_minor: int, sign: str, why: str, snapshot: CompanySnapshot):
    old = group.daily_budget
    pct = abs(new_minor - old.amount_minor) * 100 // old.amount_minor
    return OptimizerDecision(
        "set_budget", group.id, Money(new_minor, old.currency),
        f"{_title(group)}: {sign}{pct}% бюджета (с {format_ru(old)} до "
        f"{format_ru(Money(new_minor, old.currency))}) — {why}")


def _cuts(weak: list[GroupState], target: Money, snapshot: CompanySnapshot, *, has_strong: bool) -> list[tuple[OptimizerDecision, int]]:
    pct = snapshot.rules.budget_change_max_pct
    cuts = []
    for group in weak:
        floor = snapshot.min_budget(group.goal_kind).amount_minor
        cut = min(_pct_of(group.daily_budget.amount_minor, pct), group.daily_budget.amount_minor - floor)
        if cut > 0 and _can_change_budget(group, snapshot):
            new = group.daily_budget.amount_minor - cut
            outcome = TRANSFER_TEXT if has_strong else NO_STRONG_TEXT
            why = f"{_cpa_text(group, target, snapshot)}; {outcome}."
            cuts.append((_budget_move(group, new, "−", why, snapshot), cut))
    return cuts


def _raises(
    strong: list[GroupState], room: int, kind: str, why: str, snapshot: CompanySnapshot
) -> list[tuple[OptimizerDecision, GroupState]]:
    pct = snapshot.rules.budget_change_max_pct
    raised = []
    for group in strong:
        gain = min(_pct_of(group.daily_budget.amount_minor, pct), room)
        if gain <= 0:
            continue
        room -= gain
        decision = _budget_move(group, group.daily_budget.amount_minor + gain, "+", why, snapshot)
        raised.append((replace(decision, kind=kind), group))  # type: ignore[arg-type]
    return raised


def decide(
    snapshot: CompanySnapshot, *, target_cpa: Money, rules=None
) -> list[OptimizerDecision]:
    """Решения по группам и объявлениям: выключение, перенос бюджета, масштабирование, выгорание."""
    if rules is not None:
        snapshot = replace(snapshot, rules=rules)
    mature = [g for g in snapshot.groups if _is_mature(g, snapshot, target_cpa)]
    kills = _group_kills(mature, target_cpa, snapshot)
    killed = {d.target_id for d in kills}
    survivors = [g for g in mature if g.id not in killed]
    ad_kills = [d for g in survivors if g.results > 0 for d in _ad_kills(g, target_cpa, snapshot)]
    strong = sorted(
        (g for g in survivors if cpa_at_most(g.spend, g.results, target_cpa, snapshot.rules.strong_cpa_ratio)),
        key=lambda g: strength_key(g.spend, g.results))
    strong_ids = {g.id for g in strong}
    weak = [g for g in survivors if g.id not in strong_ids]
    cuts = _cuts(weak, target_cpa, snapshot, has_strong=bool(strong))
    freed = sum(g.daily_budget.amount_minor for g in mature if g.id in killed) + sum(c for _, c in cuts)
    movers = [g for g in strong if _can_change_budget(g, snapshot)]
    moved = _raises(movers, freed, "set_budget",
                    "цена результата лучше целевой; перенос от слабых групп, общая сумма не растёт.", snapshot)
    moved_ids = {g.id for _, g in moved}
    projected = (snapshot.active_total - sum(g.daily_budget.amount_minor for g in mature if g.id in killed)
                 - sum(c for _, c in cuts) + sum(d.new_budget.amount_minor - g.daily_budget.amount_minor
                                                 for d, g in moved))
    headroom = snapshot.daily_cap.amount_minor - projected
    scalers = [g for g in movers if g.id not in moved_ids]
    scaled = _raises(scalers, headroom, "scale",
                     "цена результата лучше целевой, потолок на сегодня позволяет.", snapshot)
    limit = _comma(snapshot.rules.fatigue_frequency)
    fatigue = [
        OptimizerDecision(
            "fatigue_warning", g.id, None,
            f"{_title(g)}: частота показов {_comma(g.frequency)} при пороге {limit} — аудитория "
            "устаёт, стоит заменить креативы.")
        for g in snapshot.groups if snapshot.is_spending(g) and g.id not in killed and g.frequency > snapshot.rules.fatigue_frequency
    ]
    return [*kills, *ad_kills, *(d for d, _ in cuts), *(d for d, _ in moved), *(d for d, _ in scaled), *fatigue]


def apply_decision(snapshot: CompanySnapshot, decision: OptimizerDecision) -> CompanySnapshot:
    """Снимок после решения (новая копия): нужен, чтобы проверять пакет решений по очереди."""
    groups = []
    for group in snapshot.groups:
        if group.id == decision.target_id and decision.kind == "pause_group":
            group = replace(group, active=False)
        elif group.id == decision.target_id and decision.new_budget is not None:
            group = replace(group, daily_budget=decision.new_budget, last_budget_change_at=snapshot.now)
        elif decision.kind == "pause_ad" and any(a.id == decision.target_id for a in group.ads):
            ads = tuple(replace(a, active=False) if a.id == decision.target_id else a for a in group.ads)
            group = replace(group, ads=ads)
        groups.append(group)
    return replace(snapshot, groups=tuple(groups))


def _priority(decision: OptimizerDecision, snapshot: CompanySnapshot) -> int:
    """Порядок проверки: выключения, затем снижения, затем переносы-повышения, затем масштабирование."""
    if decision.kind in ("pause_group", "pause_ad"):
        return 0
    if decision.kind == "scale":
        return 3
    group = snapshot.group(decision.target_id)
    lowers = decision.new_budget is not None and group is not None and decision.new_budget < group.daily_budget
    return 1 if lowers else 2


def check_decisions(
    decisions: list[OptimizerDecision], snapshot: CompanySnapshot,
    *, approved_keys: frozenset[str] = frozenset(),
) -> list[tuple[OptimizerDecision, Decision]]:
    """Пакет решений через `check_action`. Вердикты возвращаются в порядке ИСПОЛНЕНИЯ:
    паузы, снижения, переносы, масштабирование; Исполнитель идёт строго в нём.

    Состояние обновляется только принятыми (`Allowed`) решениями: отклонённое снижение не
    «освобождает» бюджет под повышение. Повышение-перенос, не выводящее сумму выше исходной,
    кнопки не требует; `scale` и любой рост сверх исходной суммы — требуют (confirm) или
    отклоняются потолком. Подтверждение действует только на конкретное действие.
    """
    baseline = snapshot.active_total
    running = snapshot
    verdicts: dict[int, Decision] = {}
    order = sorted(range(len(decisions)), key=lambda i: (_priority(decisions[i], snapshot), i))
    for index in order:
        decision = decisions[index]
        action = decision.to_action()
        if action is None:
            continue
        strict = None if decision.kind == "scale" else baseline
        # подтверждение выдано на то, что пользователь видел в исходном снимке
        # ключ в NeedsApproval.key и ключ в approved_keys считаются на одном — исходном — снимке
        verdict = check_in_batch(action, running, approved_keys=approved_keys, spend_baseline=strict,
                                 key_snapshot=snapshot)
        verdicts[index] = verdict
        if isinstance(verdict, Allowed):
            running = apply_decision(running, decision)
    return [(decisions[i], verdicts[i]) for i in order if i in verdicts]
