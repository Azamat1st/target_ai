"""Типы политики: решения, состояние группы, снимок компании."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from target_ai.clock import to_account_time
from target_ai.money import CurrencyMismatch, Money

from .pacing import daily_cap as _daily_cap
from .pacing import days_left as _days_left
from .rules import META_DAILY_OVERSPEND, OptimizerRules

Mode = Literal["confirm", "autonomous"]
Level = Literal["demo", "test", "live"]
GoalKind = Literal["imp", "high_freq"]  # какой минимум Meta применяется к группе


@dataclass(frozen=True)
class Allowed:
    """Действие разрешено."""


@dataclass(frozen=True)
class Denied:
    reason_ru: str
    code: str


@dataclass(frozen=True)
class NeedsApproval:
    kind: str
    summary_ru: str
    key: str = ""  # action_key действия в том состоянии, которое видит пользователь на кнопке


Decision = Allowed | Denied | NeedsApproval


@dataclass(frozen=True, kw_only=True)
class AdState:
    id: str
    impressions: int = 0
    spend: Money | None = None
    results: int = 0
    active: bool = True


@dataclass(frozen=True, kw_only=True)
class GroupState:
    """Группа как её видит политика: бюджет, возраст, расход и результаты с момента включения."""

    id: str
    daily_budget: Money
    name: str = ""
    campaign_id: str = ""
    active: bool = True
    goal_kind: GoalKind = "high_freq"
    spent_today: Money | None = None
    last_budget_change_at: datetime | None = None
    active_since: datetime | None = None
    spend: Money | None = None
    results: int = 0
    frequency: Decimal = Decimal(0)
    ads: tuple[AdState, ...] = ()


@dataclass(frozen=True, kw_only=True)
class CampaignState:
    id: str
    spent: Money
    spend_cap: Money | None = None
    active: bool = True


@dataclass(frozen=True, kw_only=True)
class CompanySnapshot:
    company_id: int
    currency: str
    timezone: str
    now: datetime
    mode: Mode
    level: Level
    is_demo: bool
    halted: bool
    monthly_limit: Money
    spent_month: Money
    min_budget_imp: Money
    min_budget_high_freq: Money
    min_spend_cap: Money | None = None
    groups: tuple[GroupState, ...] = ()
    campaigns: tuple[CampaignState, ...] = ()
    own_object_ids: frozenset[str] = frozenset()
    rules: OptimizerRules = field(default_factory=OptimizerRules)
    overspend: Decimal = META_DAILY_OVERSPEND

    def __post_init__(self) -> None:
        if self.now.tzinfo is None:
            raise ValueError("now должен быть aware datetime.")
        if self.overspend < META_DAILY_OVERSPEND:
            raise ValueError(f"Поправка на перерасход не может быть меньше {META_DAILY_OVERSPEND}.")
        for money in self._all_money():
            if money.currency != self.currency:
                raise CurrencyMismatch(
                    f"В снимке компании смешаны валюты {self.currency} и {money.currency}."
                )
            if money.amount_minor < 0:
                raise ValueError("Суммы в снимке компании не могут быть отрицательными.")
        ids = [g.id for g in self.groups] + [c.id for c in self.campaigns]
        ids += [a.id for g in self.groups for a in g.ads]
        if len(ids) != len(set(ids)):
            raise ValueError("Идентификаторы групп, кампаний и объявлений в снимке должны быть уникальны.")
        self._check_campaign_links()

    def _check_campaign_links(self) -> None:
        """Принадлежность группы кампании задаёт только `GroupState.campaign_id`."""
        if any(not c.id.strip() for c in self.campaigns):
            raise ValueError("Идентификатор кампании в снимке не может быть пустым.")
        known = {c.id for c in self.campaigns}
        for group in self.groups:
            if group.campaign_id and group.campaign_id not in known:
                raise ValueError(f"Группа {group.id} ссылается на кампанию {group.campaign_id}, которой нет в снимке.")

    def _all_money(self) -> list[Money]:
        found = [self.monthly_limit, self.spent_month, self.min_budget_imp, self.min_budget_high_freq]
        if self.min_spend_cap is not None:
            found.append(self.min_spend_cap)
        for group in self.groups:
            found.append(group.daily_budget)
            found.extend(m for m in (group.spent_today, group.spend) if m is not None)
            found.extend(a.spend for a in group.ads if a.spend is not None)
        for campaign in self.campaigns:
            found.append(campaign.spent)
            if campaign.spend_cap is not None:
                found.append(campaign.spend_cap)
        return found

    @property
    def today(self) -> date:
        return to_account_time(self.now, self.timezone).date()

    @property
    def days_left(self) -> int:
        return _days_left(self.today)

    @property
    def remaining(self) -> Money:
        return Money(max(0, (self.monthly_limit - self.spent_month).amount_minor), self.currency)

    @property
    def daily_cap(self) -> Money:
        return _daily_cap(self.monthly_limit, self.spent_month, self.today, self.overspend)

    def campaign_of(self, group: GroupState) -> CampaignState | None:
        return self.campaign(group.campaign_id) if group.campaign_id else None

    def groups_of(self, campaign_id: str) -> tuple[GroupState, ...]:
        """Группы кампании, вычисленные из снимка: единственный источник состава для всех расчётов."""
        return tuple(g for g in self.groups if g.campaign_id == campaign_id)

    def is_spending(self, group: GroupState) -> bool:
        """Группа тратит деньги, только если активны и она сама, и её кампания."""
        campaign = self.campaign_of(group)
        return group.active and (campaign is None or campaign.active)

    @property
    def active_total(self) -> int:
        """Сумма дневных бюджетов тратящих групп (активна и группа, и кампания), мин. единицы."""
        return sum(g.daily_budget.amount_minor for g in self.groups if self.is_spending(g))

    def min_budget(self, goal_kind: GoalKind) -> Money:
        return self.min_budget_imp if goal_kind == "imp" else self.min_budget_high_freq

    def group(self, group_id: str) -> GroupState | None:
        return next((g for g in self.groups if g.id == group_id), None)

    def has_ad(self, ad_id: str) -> bool:
        return any(a.id == ad_id for g in self.groups for a in g.ads)

    def campaign(self, campaign_id: str) -> CampaignState | None:
        return next((c for c in self.campaigns if c.id == campaign_id), None)
