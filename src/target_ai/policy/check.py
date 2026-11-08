"""Проверка действия перед выполнением (спека §3): восемь проверок, первая сработавшая решает."""

from __future__ import annotations

from collections.abc import Callable

from target_ai.money import Money, format_ru

from .actions import (
    ALLOWED_POSITIONS, INSTAGRAM, MOBILE, Action, Activate, CreateObject, Delete, Launch, Pause,
    SetDailyBudget, SetMonthlyLimit, SetSpendCap, Targeting,
)
from .rules import BUDGET_FLOOR_OVER_SPENT_PCT
from .spend import affected_groups, increases_spend, projected_total
from .types import Allowed, CompanySnapshot, Decision, Denied, NeedsApproval

_KNOWN_ACTIONS = (
    Launch, Activate, Pause, SetDailyBudget, SetSpendCap, SetMonthlyLimit, CreateObject, Delete,
)
_Step = Callable[[Action, CompanySnapshot], Denied | None]


def _fmt(minor: int, snapshot: CompanySnapshot) -> str:
    return format_ru(Money(minor, snapshot.currency))


def _enables_or_increases(action: Action, snapshot: CompanySnapshot) -> bool:
    return isinstance(action, Launch | Activate) or increases_spend(action, snapshot)


def _touched_state(action: Action, snapshot: CompanySnapshot) -> list[tuple[str, bool, int, bool]]:
    if isinstance(action, Activate):
        groups = affected_groups(action, snapshot) or []
    elif isinstance(action, SetDailyBudget):
        groups = [g for g in [snapshot.group(action.group_id)] if g is not None]
    else:
        groups = []
    return [(g.id, g.active, g.daily_budget.amount_minor, snapshot.is_spending(g)) for g in groups]


def action_key(action: Action, snapshot: CompanySnapshot) -> str:
    """Ключ подтверждения: само действие плюс то, что пользователь видел на кнопке.

    В ключе — суммы «было → станет», затронутые группы с их бюджетами и состоянием, защитные
    лимиты кампаний и месячный лимит. Любое расхождение со снимком даёт другой ключ.
    """
    caps = [(c.id, c.spend_cap.amount_minor if c.spend_cap else None) for c in snapshot.campaigns]
    launch = ([(g.id, g.budget.amount_minor, g.goal_kind) for g in action.groups], action.tag) \
        if isinstance(action, Launch) else None
    parts = [
        repr(action), f"launch={launch}", f"company={snapshot.company_id}", f"before={snapshot.active_total}",
        f"after={projected_total(action, snapshot)}", f"limit={snapshot.monthly_limit.amount_minor}",
        f"touched={_touched_state(action, snapshot)}", f"caps={caps}",
    ]
    return "|".join(parts)


def _known(action: object, snapshot: CompanySnapshot) -> Denied | None:
    if not isinstance(action, _KNOWN_ACTIONS):
        return Denied(f"Неизвестный тип действия {type(action).__name__}: по умолчанию запрещено.",
                      "unknown_action")
    return None


def _halted(action: Action, snapshot: CompanySnapshot) -> Denied | None:
    if snapshot.halted and _enables_or_increases(action, snapshot):
        return Denied("Стоп-кран включён: пока вы не нажмёте «Возобновить систему», "
                      "включать кампании и увеличивать расход нельзя.", "halted")
    return None


def _touched_ids(action: Action) -> tuple[str, ...]:
    match action:
        case Activate(object_id=oid) | Pause(object_id=oid) | Delete(object_id=oid):
            return (oid,)
        case SetDailyBudget(group_id=gid):
            return (gid,)
        case SetSpendCap(campaign_id=cid):
            return (cid,)
        case CreateObject(parent_id=pid) if pid is not None:
            return (pid,)
    return ()


def _ownership(action: Action, snapshot: CompanySnapshot) -> Denied | None:
    for object_id in _touched_ids(action):
        if object_id not in snapshot.own_object_ids:
            return Denied(f"Объект {object_id} создан не этой системой: чужие объекты "
                          "аккаунта бот не трогает.", "not_own")
    return None


def _level(action: Action, snapshot: CompanySnapshot) -> Denied | None:
    if (snapshot.level == "demo") != snapshot.is_demo:
        what = "демо-компания" if snapshot.is_demo else "обычная компания"
        return Denied(f"Уровень работы «{snapshot.level}» не подходит: это {what}.", "level_mismatch")
    return None


def _targeting_problem(targeting: Targeting | None) -> str | None:
    if targeting is None:
        return "у группы нет таргетинга с площадками"
    if targeting.publisher_platforms != (INSTAGRAM,):
        return "разрешён только Instagram"
    if not targeting.instagram_positions or not set(targeting.instagram_positions) <= ALLOWED_POSITIONS:
        return "не заданы допустимые места показа в Instagram"
    if targeting.device_platforms != (MOBILE,):
        return "разрешены только мобильные устройства"
    if targeting.placement_soft_opt_out:
        return "автоматические площадки отключены правилом"
    return None


def _placements(action: Action, snapshot: CompanySnapshot) -> Denied | None:
    if isinstance(action, Launch):
        targetings: list[Targeting | None] = [g.targeting for g in action.groups]
    elif isinstance(action, CreateObject) and action.kind == "group":
        targetings = [action.targeting]
    else:
        return None
    for targeting in targetings:
        problem = _targeting_problem(targeting)
        if problem:
            return Denied(f"Таргетинг отклонён: {problem}.", "placements")
    return None


def _money_fields(action: Action) -> list[Money]:
    match action:
        case Launch(groups=groups, spend_cap=cap):
            return [g.budget for g in groups] + ([cap] if cap is not None else [])
        case SetDailyBudget(new_budget=m) | SetSpendCap(cap=m) | SetMonthlyLimit(new_limit=m):
            return [m]
        case CreateObject(budget=m) if m is not None:
            return [m]
    return []


def _spend_cap_problem(cap: Money, campaign_spent: int, snapshot: CompanySnapshot) -> Denied | None:
    floor = snapshot.min_spend_cap.amount_minor if snapshot.min_spend_cap else 0
    if cap.amount_minor < floor:
        return Denied(f"Защитный лимит {format_ru(cap)} меньше минимума Meta "
                      f"{_fmt(floor, snapshot)}.", "spend_cap")
    allowed = max(floor, campaign_spent + snapshot.remaining.amount_minor)
    if cap.amount_minor > allowed:
        return Denied(f"Защитный лимит {format_ru(cap)} выше допустимого {_fmt(allowed, snapshot)}: "
                      "расход кампании плюс остаток месячного лимита.", "spend_cap")
    return None


def _budget_values(action: Action) -> list[int]:
    """Дневные бюджеты, которые действие задаёт группам."""
    match action:
        case Launch(groups=groups):
            return [g.budget.amount_minor for g in groups]
        case SetDailyBudget(new_budget=m) | CreateObject(budget=m) if m is not None:
            return [m.amount_minor]
    return []


def _is_shrinking(action: Action, snapshot: CompanySnapshot) -> bool:
    if not isinstance(action, SetDailyBudget):
        return False
    group = snapshot.group(action.group_id)
    return group is not None and action.new_budget < group.daily_budget


def _cap_problem(action: Action, snapshot: CompanySnapshot) -> Denied | None:
    after = projected_total(action, snapshot)
    if after is None:
        return Denied("В снимке компании нет данных об этом объекте: сумму проверить нельзя.",
                      "unknown_group")
    if _is_shrinking(action, snapshot):
        return None  # снижение не делает хуже, даже если компания уже выше потолка
    cap = snapshot.daily_cap.amount_minor
    biggest = max(_budget_values(action), default=0)
    grows = after > snapshot.active_total
    if biggest > cap or (grows and after > cap):
        shown = max(biggest, after) if grows else biggest
        return Denied(f"Сумма дневных бюджетов {_fmt(shown, snapshot)} выше потолка на сегодня "
                      f"{_fmt(cap, snapshot)}: остаток лимита делится на оставшиеся дни.", "over_cap")
    return None


def _group_budgets(action: Action) -> list[tuple[Money, str]]:
    match action:
        case Launch(groups=groups):
            return [(g.budget, g.goal_kind) for g in groups]
        case CreateObject(kind="group", budget=m, goal_kind=kind) if m is not None:
            return [(m, kind or "high_freq")]
        case SetDailyBudget(group_id=_):
            return []
    return []


def _min_problem(action: Action, snapshot: CompanySnapshot) -> Denied | None:
    pairs = _group_budgets(action)
    if isinstance(action, SetDailyBudget):
        group = snapshot.group(action.group_id)
        pairs = [(action.new_budget, group.goal_kind)] if group else []
    for budget, goal_kind in pairs:
        minimum = snapshot.min_budget(goal_kind)  # type: ignore[arg-type]
        if budget < minimum:
            return Denied(f"Дневной бюджет группы {format_ru(budget)} меньше минимума Meta "
                          f"{format_ru(minimum)}.", "below_min")
    return None


def _below_spent(action: Action, snapshot: CompanySnapshot) -> Denied | None:
    if not isinstance(action, SetDailyBudget):
        return None
    group = snapshot.group(action.group_id)
    if group is None or group.spent_today is None or action.new_budget >= group.daily_budget:
        return None
    spent = group.spent_today.amount_minor
    floor = -(-spent * (100 + BUDGET_FLOOR_OVER_SPENT_PCT) // 100)
    if action.new_budget.amount_minor < floor:
        return Denied(f"Бюджет нельзя снизить до {format_ru(action.new_budget)}: сегодня уже "
                      f"потрачено {format_ru(group.spent_today)}, Meta требует запас 10% "
                      f"(от {_fmt(floor, snapshot)}). Если нужно меньше — группу ставят на паузу.",
                      "below_spent")
    return None


def _activation_problem(action: Action, snapshot: CompanySnapshot) -> Denied | None:
    if not isinstance(action, Activate):
        return None
    if projected_total(action, snapshot) is None:
        return Denied(f"Объекта {action.object_id} вида «{action.object_kind}» нет в снимке компании "
                      "(или у кампании нет своих групп): включать нечего.", "unknown_object")
    if snapshot.remaining.amount_minor == 0 or snapshot.daily_cap.amount_minor == 0:
        return Denied("Лимит месяца исчерпан или потолок на сегодня равен нулю: включать нельзя.",
                      "month_exhausted")
    return None


def _amounts(action: Action, snapshot: CompanySnapshot) -> Denied | None:
    if isinstance(action, CreateObject) and action.kind == "campaign" and action.budget is not None:
        return Denied("Бюджет задаётся только на уровне групп, у кампании его быть не должно.",
                      "campaign_budget")
    activation = _activation_problem(action, snapshot)
    if activation is not None:
        return activation
    for money in _money_fields(action):
        if money.currency != snapshot.currency:
            return Denied(f"Сумма в валюте {money.currency}, а рекламный аккаунт — в "
                          f"{snapshot.currency}: пересчёта по курсу нет.", "currency")
        if money.amount_minor <= 0:
            return Denied("Сумма должна быть больше нуля.", "non_positive")
    if isinstance(action, SetSpendCap):
        campaign = snapshot.campaign(action.campaign_id)
        spent = campaign.spent.amount_minor if campaign else 0
        return _spend_cap_problem(action.cap, spent, snapshot)
    if isinstance(action, Launch) and action.spend_cap is not None:
        problem = _spend_cap_problem(action.spend_cap, 0, snapshot)
        if problem:
            return problem
    if isinstance(action, SetMonthlyLimit | Pause | Delete):
        return None
    return _cap_problem(action, snapshot) or _min_problem(action, snapshot) or _below_spent(action, snapshot)


def _budget_change(action: Action, snapshot: CompanySnapshot) -> Denied | None:
    if not isinstance(action, SetDailyBudget):
        return None
    group = snapshot.group(action.group_id)
    if group is None:
        return None
    old, new = group.daily_budget.amount_minor, action.new_budget.amount_minor
    if action.lowers_for_limit and new < old:
        return None
    rules = snapshot.rules
    if abs(new - old) * 100 > old * rules.budget_change_max_pct:
        return Denied(f"Бюджет группы можно менять не больше чем на {rules.budget_change_max_pct}% "
                      f"за раз: было {_fmt(old, snapshot)}, просят {_fmt(new, snapshot)}.",
                      "change_too_big")
    last = group.last_budget_change_at
    hours = rules.budget_change_min_interval_hours
    if last is not None and (snapshot.now - last).total_seconds() < hours * 3600:
        return Denied(f"Бюджет группы уже менялся меньше {hours} ч назад: следующее изменение позже.",
                      "too_soon")
    return None


_STEPS: tuple[_Step, ...] = (_known, _halted, _ownership, _level, _placements, _amounts, _budget_change)

_KIND_BY_ACTION = {
    Launch: "launch", Activate: "resume", SetDailyBudget: "budget_increase",
    SetSpendCap: "spend_cap_increase", SetMonthlyLimit: "monthly_limit_increase",
}


def _approval_summary(action: Action, snapshot: CompanySnapshot) -> str:
    cap = format_ru(snapshot.daily_cap)
    if isinstance(action, SetMonthlyLimit):
        return (f"Месячный лимит вырастет с {format_ru(snapshot.monthly_limit)} "
                f"до {format_ru(action.new_limit)}.")
    if isinstance(action, SetSpendCap):
        return f"Защитный лимит кампании вырастет до {format_ru(action.cap)}."
    after = projected_total(action, snapshot) or 0
    return (f"Сумма дневных бюджетов вырастет с {_fmt(snapshot.active_total, snapshot)} "
            f"до {_fmt(after, snapshot)} (потолок на сегодня {cap}).")


def _caps_exceed_reserve(snapshot: CompanySnapshot, campaign_id: str | None, new_cap: Money) -> bool:
    """Сумма защитных лимитов всех кампаний компании выше «расход кампаний + остаток месяца»."""
    others = sum(c.spend_cap.amount_minor for c in snapshot.campaigns
                 if c.id != campaign_id and c.spend_cap is not None)
    reserve = sum(c.spent.amount_minor for c in snapshot.campaigns) + snapshot.remaining.amount_minor
    return others + new_cap.amount_minor > reserve


def _grows(action: Action, snapshot: CompanySnapshot, baseline: int | None) -> bool:
    """Увеличивает ли расход; с `baseline` — относительно суммы в начале пакета решений."""
    if baseline is None or isinstance(action, SetSpendCap | SetMonthlyLimit):
        return increases_spend(action, snapshot)
    projected = projected_total(action, snapshot)
    return projected is None or projected > baseline


def _needs_button(action: Action, snapshot: CompanySnapshot, baseline: int | None = None) -> bool:
    if isinstance(action, Pause | Delete | CreateObject):
        return False  # выключение, удаление и создание на паузе деньги не увеличивают
    if isinstance(action, SetSpendCap):
        campaign = snapshot.campaign(action.campaign_id)
        reserve = (campaign.spent.amount_minor if campaign else 0) + snapshot.remaining.amount_minor
        above_reserve = action.cap.amount_minor > reserve  # поднятый до минимума Meta — всегда кнопка
        return (above_reserve or _caps_exceed_reserve(snapshot, action.campaign_id, action.cap)
                or (snapshot.mode == "confirm" and increases_spend(action, snapshot)))
    if isinstance(action, SetMonthlyLimit):
        return increases_spend(action, snapshot)
    if isinstance(action, Launch):
        # Защитный лимит выше остатка (подняли до минимума Meta) — кнопка даже в «Автономном».
        above_remaining = action.spend_cap is not None and action.spend_cap > snapshot.remaining
        caps_over = action.spend_cap is not None and _caps_exceed_reserve(snapshot, None, action.spend_cap)
        return above_remaining or caps_over or snapshot.mode == "confirm"
    return snapshot.mode == "confirm" and _grows(action, snapshot, baseline)


def check_action(
    action: Action, snapshot: CompanySnapshot, *, approved_keys: frozenset[str] = frozenset(),
) -> Decision:
    """Allowed / Denied(причина) / NeedsApproval. `approved_keys` — потреблённые ключи
    (`action_key(action, snapshot)`); ключ учитывает состояние, поэтому устаревший не подходит."""
    return check_in_batch(action, snapshot, approved_keys=approved_keys, spend_baseline=None)


def check_in_batch(
    action: Action, snapshot: CompanySnapshot, *, approved_keys: frozenset[str] = frozenset(),
    spend_baseline: int | None = None, key_snapshot: CompanySnapshot | None = None,
) -> Decision:
    """Allowed / Denied(причина) / NeedsApproval. `approved_keys` — ключи (`action_key`) действий, чьё подтверждение уже потреблено.

    `spend_baseline` — сумма дневных бюджетов в начале пакета решений: перенос бюджета внутри
    пакета не считается ростом расхода, пока итог не выше этой суммы. `key_snapshot` — снимок,
    на котором считается ключ подтверждения (исходный снимок пакета; по умолчанию `snapshot`).
    """
    for step in _STEPS:
        denial = step(action, snapshot)
        if denial is not None:
            return denial
    key = action_key(action, key_snapshot or snapshot)
    if key in approved_keys or not _needs_button(action, snapshot, spend_baseline):
        return Allowed()
    return NeedsApproval(_KIND_BY_ACTION.get(type(action), "approval"), _approval_summary(action, snapshot),
                         key)
