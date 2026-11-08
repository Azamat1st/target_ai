"""Что считается увеличением расхода (спека §3, R51): рост суммы дневных бюджетов активных групп."""

from __future__ import annotations

from .actions import Action, Activate, Launch, SetDailyBudget, SetMonthlyLimit, SetSpendCap
from .types import CompanySnapshot, GroupState


def affected_groups(action: Activate, snapshot: CompanySnapshot) -> list[GroupState] | None:
    """Группы, которые включение может запустить в трату; None — объект не найден в снимке."""
    if action.object_kind == "group":
        group = snapshot.group(action.object_id)
        return None if group is None else [group]
    if action.object_kind == "ad":
        owner = next((g for g in snapshot.groups if any(a.id == action.object_id for a in g.ads)), None)
        return None if owner is None else [owner]
    campaign = snapshot.campaign(action.object_id)
    groups = snapshot.groups_of(action.object_id)
    if campaign is None or not groups:
        return None  # кампания без своих групп в снимке: сумму посчитать нельзя
    return list(groups)


def projected_total(action: Action, snapshot: CompanySnapshot) -> int | None:
    """Сумма дневных бюджетов тратящих групп после действия (минимальные единицы).

    None — действие ссылается на объект, о котором в снимке нет данных.
    """
    total = snapshot.active_total
    if isinstance(action, Launch):
        return total + sum(group.budget.amount_minor for group in action.groups)
    if isinstance(action, Activate):
        groups = affected_groups(action, snapshot)
        if groups is None:
            return None
        return total + sum(g.daily_budget.amount_minor for g in groups if not snapshot.is_spending(g))
    if isinstance(action, SetDailyBudget):
        group = snapshot.group(action.group_id)
        if group is None:
            return None
        if not snapshot.is_spending(group):
            return total
        return total - group.daily_budget.amount_minor + action.new_budget.amount_minor
    return total


def increases_spend(action: Action, snapshot: CompanySnapshot) -> bool:
    """Увеличивает ли действие расход; неизвестный объект считается увеличением (безопаснее)."""
    if isinstance(action, SetMonthlyLimit):
        return action.new_limit > snapshot.monthly_limit
    if isinstance(action, SetSpendCap):
        campaign = snapshot.campaign(action.campaign_id)
        if campaign is None:
            return True
        return campaign.spend_cap is not None and action.cap > campaign.spend_cap
    projected = projected_total(action, snapshot)
    return projected is None or projected > snapshot.active_total
