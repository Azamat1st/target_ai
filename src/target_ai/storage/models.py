"""Модели хранилища: неизменяемые dataclass (обновление — через dataclasses.replace)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from target_ai.money import Money

from .mapping import dt_field, dt_required, json_field, money_field


@dataclass(frozen=True, kw_only=True)
class Company:
    name: str
    id: int = 0
    instagram_handle: str | None = None
    website: str | None = None
    competitors: list = json_field(list)
    ad_account_id: str | None = None
    page_id: str | None = None
    ig_user_id: str | None = None
    currency: str | None = None
    timezone: str | None = None
    monthly_limit: Money | None = money_field()
    target_cpa: Money | None = money_field()
    mode: str = "confirm"
    status: str = "active"
    profile: dict = json_field(dict)
    created_at: datetime | None = dt_field()


@dataclass(frozen=True, kw_only=True)
class Fact:
    company_id: int
    text: str
    source: str
    id: int = 0
    kind: str = "general"
    created_at: datetime | None = dt_field()


@dataclass(frozen=True, kw_only=True)
class Passport:
    company_id: int
    version: int
    data: dict = json_field(dict)
    id: int = 0
    created_at: datetime | None = dt_field()


@dataclass(frozen=True, kw_only=True)
class MediaAsset:
    company_id: int
    kind: str
    id: int = 0
    source_ref: str | None = None
    path: str | None = None
    caption: str | None = None
    meta_id: str | None = None
    data: dict = json_field(dict)
    created_at: datetime | None = dt_field()


@dataclass(frozen=True, kw_only=True)
class Plan:
    company_id: int
    version: int
    id: int = 0
    status: str = "draft"
    data: dict = json_field(dict)
    created_at: datetime | None = dt_field()
    updated_at: datetime | None = dt_field()


@dataclass(frozen=True, kw_only=True)
class Launch:
    plan_id: int
    company_id: int
    id: int = 0
    status: str = "started"
    error: str | None = None
    started_at: datetime | None = dt_field()
    finished_at: datetime | None = dt_field()


@dataclass(frozen=True, kw_only=True)
class LaunchStep:
    launch_id: int
    step: str
    id: int = 0
    status: str = "pending"
    meta_id: str | None = None
    data: dict = json_field(dict)
    updated_at: datetime | None = dt_field()


@dataclass(frozen=True, kw_only=True)
class MetaObject:
    company_id: int
    meta_id: str
    kind: str
    id: int = 0
    launch_id: int | None = None
    parent_meta_id: str | None = None
    name: str = ""
    status: str = "PAUSED"
    daily_budget: Money | None = money_field()
    last_budget_change_at: datetime | None = dt_field()
    created_at: datetime | None = dt_field()


@dataclass(frozen=True, kw_only=True)
class Approval:
    id: str
    kind: str
    expires_at: datetime = dt_required()
    company_id: int | None = None
    payload: dict = json_field(dict)
    status: str = "pending"
    created_at: datetime | None = dt_field()
    consumed_at: datetime | None = dt_field()


@dataclass(frozen=True, kw_only=True)
class DailyStat:
    meta_id: str
    company_id: int
    date: str
    id: int = 0
    spend: Money | None = money_field()
    impressions: int = 0
    reach: int = 0
    frequency: float = 0.0
    clicks: int = 0
    results: int = 0


@dataclass(frozen=True, kw_only=True)
class HistoryEntry:
    company_id: int
    kind: str
    id: int = 0
    hypothesis: str | None = None
    outcome: str | None = None
    data: dict = json_field(dict)
    created_at: datetime | None = dt_field()


@dataclass(frozen=True, kw_only=True)
class AuditEntry:
    actor: str
    action: str
    id: int = 0
    company_id: int | None = None
    data: dict = json_field(dict)
    created_at: datetime | None = dt_field()


@dataclass(frozen=True, kw_only=True)
class Job:
    kind: str
    due_at: datetime = dt_required()
    id: int = 0
    company_id: int = 0
    last_run_at: datetime | None = dt_field()


@dataclass(frozen=True, kw_only=True)
class DialogState:
    chat_id: int
    state: str = ""
    data: dict = json_field(dict)
    updated_at: datetime | None = dt_field()

