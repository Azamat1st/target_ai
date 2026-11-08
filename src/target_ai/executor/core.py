"""Общая основа Исполнителя: снимок, проверка политики, журнал, реестр, уведомления."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from target_ai import policy as default_policy
from target_ai.clock import Clock, to_account_time
from target_ai.meta.errors import MetaError
from target_ai.money import Money
from target_ai.policy import (
    AdState, CampaignState, CompanySnapshot, Denied, GroupState, NeedsApproval, OptimizerRules,
)
from target_ai.storage import AuditEntry, Company, Database, MetaObject

from .errors import ApprovalInvalid, ApprovalRequired, ExecutorError, PolicyDenied
from target_ai.config.runtime import DEFAULT_HALTED, DEFAULT_LEVEL, SETTING_HALTED, SETTING_INSTALL_ID, SETTING_LEVEL, SETTING_OVERSPEND, SETTING_RULES

log = logging.getLogger(__name__)

DEAD_STATUSES = frozenset({"DELETED", "ARCHIVED"})
RETIRED = "RETIRED"  # снятая предыдущая кампания: на паузе навсегда, не включается
SAFE_PAUSE_IGNORED_CODES = frozenset({"level_mismatch"})  # пауза снижает риск: уровень ей не помеха
ACCOUNT_CACHE_TTL = timedelta(minutes=10)
EPOCH_DATE = "0001-01-01"


@dataclass(frozen=True)
class AccountInfo:
    currency: str
    timezone: str
    min_imp: Money
    min_high_freq: Money
    min_spend_cap: Money | None


class Core:
    def __init__(self, db: Database, gateway_factory, policy, clock: Clock, notifier) -> None:
        self._db = db
        self._factory = gateway_factory
        self._policy = policy or default_policy
        self._clock = clock
        self._notifier = notifier
        self._accounts: dict[str, tuple[datetime, AccountInfo]] = {}
        self._company_locks: dict[int, asyncio.Lock] = {}

    # ----------------------------------------------------------------- общее
    def gateway(self, *, validate_only: bool = False):
        return self._factory(validate_only)

    @staticmethod
    async def io(fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    def is_halted(self) -> bool:
        return bool(self._db.settings.get(SETTING_HALTED, DEFAULT_HALTED))

    def install_id(self) -> str:
        """Случайный id установки: создаётся один раз и входит в тег каждого объекта Meta."""
        value = self._db.settings.get(SETTING_INSTALL_ID)
        if not value:
            value = uuid.uuid4().hex[:8]
            self._db.settings.set(SETTING_INSTALL_ID, value)
        return str(value)

    async def enable_object(self, actor: str, company_id: int, meta_id: str, action: str,
                            details: dict[str, Any] | None = None) -> None:
        """Единственный путь включения в Meta: флаг стоп-крана читается заново до вызова и проверяется после."""
        gateway = self.gateway()

        async def call() -> None:
            self.refuse_if_halted()
            await gateway.set_status(meta_id, "ACTIVE")

        await self.mutate(actor, company_id, action, {"object": meta_id, **(details or {})}, call)
        await self.io(self.set_registry, meta_id, status="ACTIVE")
        if self.is_halted():  # стоп-кран нажат, пока шёл запрос: включённое сразу обратно на паузу
            try:
                await self.mutate("system", company_id, "pause", {"object": meta_id},
                                  lambda: self.gateway().set_status(meta_id, "PAUSED"))
                await self.io(self.set_registry, meta_id, status="PAUSED")
            except Exception:
                log.exception("Не удалось вернуть на паузу %s после стоп-крана", meta_id)
            raise PolicyDenied("Стоп-кран включён во время запроса: объект возвращён на паузу.", "halted")

    def refuse_if_halted(self) -> None:
        """Последняя проверка перед включающим вызовом Meta: флаг читается из БД заново."""
        if self.is_halted():
            raise PolicyDenied("Стоп-кран включён: включать и увеличивать расход нельзя.", "halted")

    def company_lock(self, company_id: int) -> asyncio.Lock:
        return self._company_locks.setdefault(company_id, asyncio.Lock())

    async def notify(self, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> None:
        try:
            await self._notifier.send(None, text, buttons or [])
        except Exception:  # уведомление не должно ронять денежную операцию
            log.exception("Не удалось отправить уведомление Исполнителя")

    # ----------------------------------------------------------------- журнал
    async def audit(self, actor: str, action: str, company_id: int | None, outcome: str, /,
                    **details: Any) -> None:
        entry = AuditEntry(actor=actor, action=action, company_id=company_id,
                           data={**details, "outcome": outcome}, created_at=self._clock.now())
        await self.io(self._db.audit.create, entry)

    async def mutate(self, actor: str, company_id: int, action: str, details: dict[str, Any], call):
        """Намерение в журнал → вызов Meta → результат в журнал (с `fbtrace_id` при ошибке)."""
        await self.audit(actor, action, company_id, "intent", **details)
        try:
            result = await call()
        except MetaError as exc:
            await self.audit(actor, action, company_id, "error", message=exc.user_message_ru,
                             fbtrace_id=exc.fbtrace_id, meta_code=exc.code, meta_kind=exc.kind,
                             outcome_unknown=exc.outcome_unknown, **details)
            raise
        except Exception as exc:
            await self.audit(actor, action, company_id, "error", message=str(exc), **details)
            raise
        await self.audit(actor, action, company_id, "ok", **details)
        return result

    # ----------------------------------------------------------------- аккаунт и снимок
    async def account_info(self, company: Company) -> AccountInfo:
        account_id = company.ad_account_id
        if not account_id:
            raise ExecutorError("У компании не привязан рекламный аккаунт Meta.")
        cached = self._accounts.get(account_id)
        now = self._clock.now()
        if cached and now - cached[0] < ACCOUNT_CACHE_TTL:
            return cached[1]
        gateway = self.gateway()
        account = await gateway.get_ad_account(account_id)
        minimums = await gateway.get_minimum_budgets(account_id)
        info = AccountInfo(account.currency, account.timezone_name, minimums["imp"], minimums["high_freq"],
                           account.min_campaign_group_spend_cap)
        self._accounts[account_id] = (now, info)
        return info

    async def snapshot(self, company_id: int, *, limit: Money | None = None) -> CompanySnapshot:
        company = await self.io(self._db.companies.get, company_id)
        if company is None:
            raise ExecutorError(f"Компания {company_id} не найдена.")
        info = await self.account_info(company)
        return await self.io(self._build_snapshot, company, info, limit)

    def _build_snapshot(self, company: Company, info: AccountInfo, limit: Money | None) -> CompanySnapshot:
        now = self._clock.now()
        today = to_account_time(now, info.timezone).date()
        objects = [o for o in self._db.objects.list(company_id=company.id) if o.status not in DEAD_STATUSES]
        stats = self._db.stats.list_range(company.id, EPOCH_DATE, today.isoformat())
        zero = Money(0, info.currency)
        month_start = today.replace(day=1).isoformat()
        by_id: dict[str, list] = {}
        for row in stats:
            by_id.setdefault(row.meta_id, []).append(row)

        def spend(meta_id: str, since: str = EPOCH_DATE) -> Money:
            return Money(sum(r.spend.amount_minor for r in by_id.get(meta_id, []) if r.spend and r.date >= since),
                         info.currency)

        adsets = [o for o in objects if o.kind == "adset"]
        activation = self._activation_times(objects)
        groups = tuple(self._group_state(o, info, by_id, spend, today.isoformat(), activation, objects)
                       for o in adsets)
        campaigns = tuple(
            CampaignState(id=c.meta_id, active=c.status == "ACTIVE",
                          spent=Money(sum(spend(a.meta_id).amount_minor for a in adsets
                                          if a.parent_meta_id == c.meta_id), info.currency),
                          spend_cap=self._cap_of(c))
            for c in objects if c.kind == "campaign")
        spent_month = Money(sum(spend(a.meta_id, month_start).amount_minor for a in adsets), info.currency)
        return CompanySnapshot(
            company_id=company.id, currency=info.currency, timezone=info.timezone, now=now,
            mode=company.mode, level=self._db.settings.get(SETTING_LEVEL, DEFAULT_LEVEL),  # type: ignore[arg-type]
            is_demo=bool(company.profile.get("demo")), halted=self.is_halted(),
            monthly_limit=limit or company.monthly_limit or zero, spent_month=spent_month,
            min_budget_imp=info.min_imp, min_budget_high_freq=info.min_high_freq, min_spend_cap=info.min_spend_cap,
            groups=groups, campaigns=campaigns, own_object_ids=frozenset(o.meta_id for o in objects),
            rules=self._rules(), overspend=Decimal(str(self._db.settings.get(SETTING_OVERSPEND,
                                                                             default_policy.META_DAILY_OVERSPEND))),
        )

    def _rules(self) -> OptimizerRules:
        saved = self._db.settings.get(SETTING_RULES) or {}
        defaults = OptimizerRules()
        values = {k: (Decimal(str(v)) if isinstance(getattr(defaults, k), Decimal) else v)
                  for k, v in saved.items() if hasattr(defaults, k)}
        return replace(defaults, **values)

    def _cap_of(self, campaign: MetaObject) -> Money | None:
        steps = self._db.launch_steps.list(launch_id=campaign.launch_id, meta_id=campaign.meta_id) \
            if campaign.launch_id else []
        raw = (steps[0].data.get("spend_cap") if steps else None)
        return Money(int(raw["amount_minor"]), raw["currency"]) if raw else None

    def _activation_times(self, objects: list[MetaObject]) -> dict[int, datetime]:
        times: dict[int, datetime] = {}
        for launch_id in {o.launch_id for o in objects if o.launch_id}:
            for step in self._db.launch_steps.list(launch_id=launch_id, step="activate"):
                if step.status == "done" and step.updated_at:
                    times[launch_id] = step.updated_at
        return times

    def _group_state(self, obj, info, by_id, spend, today, activation, objects) -> GroupState:
        rows = by_id.get(obj.meta_id, [])
        today_row = next((r for r in rows if r.date == today), None)
        ads = tuple(AdState(
            id=a.meta_id, active=a.status == "ACTIVE",
            impressions=sum(r.impressions for r in by_id.get(a.meta_id, [])),
            spend=spend(a.meta_id), results=sum(r.results for r in by_id.get(a.meta_id, [])))
            for a in objects if a.kind == "ad" and a.parent_meta_id == obj.meta_id)
        goal = "high_freq"
        for step in self._db.launch_steps.list(launch_id=obj.launch_id, meta_id=obj.meta_id) if obj.launch_id else []:
            goal = step.data.get("goal_kind", goal)
        return GroupState(
            id=obj.meta_id, name=obj.name, campaign_id=obj.parent_meta_id or "", active=obj.status == "ACTIVE",
            daily_budget=obj.daily_budget or Money(0, info.currency), goal_kind=goal,  # type: ignore[arg-type]
            spent_today=today_row.spend if today_row and today_row.spend else Money(0, info.currency),
            last_budget_change_at=obj.last_budget_change_at,
            active_since=activation.get(obj.launch_id or 0) if obj.status == "ACTIVE" else None,
            spend=spend(obj.meta_id), results=sum(r.results for r in rows),
            frequency=Decimal(str(rows[-1].frequency)) if rows else Decimal(0), ads=ads)

    # ----------------------------------------------------------------- политика
    async def guard(self, action, company_id: int, *, actor: str = "user", approved: frozenset[str] = frozenset(),
                    self_approve: bool = False, safety: bool = False,
                    limit: Money | None = None, retiring: frozenset[str] = frozenset()) -> CompanySnapshot:
        """Снимок → `check_action`. Отказ — `PolicyDenied`, нужна кнопка — `ApprovalRequired`.

        `retiring` — кампании, которые первый шаг саги поставит на паузу: в производном снимке они неактивны.
        """
        snapshot = self.with_retired(await self.snapshot(company_id, limit=limit), retiring)
        keys = approved | (frozenset({self._policy.action_key(action, snapshot)}) if self_approve else frozenset())
        decision = self._policy.check_action(action, snapshot, approved_keys=keys)
        label = type(action).__name__
        if isinstance(decision, Denied):
            if safety and decision.code in SAFE_PAUSE_IGNORED_CODES:
                return snapshot
            await self.audit(actor, f"check:{label}", company_id, "denied", code=decision.code,
                             reason=decision.reason_ru)
            raise PolicyDenied(decision.reason_ru, decision.code)
        if isinstance(decision, NeedsApproval):
            raise ApprovalRequired(decision)
        return snapshot

    @staticmethod
    def with_retired(snapshot: CompanySnapshot, retiring: frozenset[str]) -> CompanySnapshot:
        """Производный снимок: перечисленные кампании неактивны. Реальное состояние не меняется."""
        if not retiring:
            return snapshot
        campaigns = tuple(replace(c, active=False) if c.id in retiring else c for c in snapshot.campaigns)
        return replace(snapshot, campaigns=campaigns)

    def peek_keys(self, approval_id: str | None, company_id: int | None, kinds: tuple[str, ...] = ()) -> frozenset[str]:
        """Ключи действий из ещё не потреблённого подтверждения (без потребления)."""
        if not approval_id:
            return frozenset()
        approval = self._db.approvals.get(approval_id)
        if (approval is None or approval.status != "pending" or approval.expires_at <= self._clock.now()
                or (approval.company_id not in (None, company_id)) or (kinds and approval.kind not in kinds)):
            return frozenset()
        payload = approval.payload
        return frozenset([*payload.get("keys", ()), *([payload["key"]] if payload.get("key") else [])])

    def consume(self, approval_id: str | None) -> None:
        """Атомарно потребляет подтверждение; не вышло — `ApprovalInvalid`."""
        if approval_id and self._db.approvals.consume(approval_id, self._clock.now()) is None:
            raise ApprovalInvalid("Подтверждение уже использовано, отменено или просрочено.")

    # ----------------------------------------------------------------- реестр
    def register(self, company_id: int, launch_id: int | None, meta_id: str, kind: str, parent: str | None,
                 name: str, budget: Money | None = None, status: str = "PAUSED") -> MetaObject:
        existing = self._db.objects.get_by_meta_id(meta_id)
        if existing:
            return existing
        return self._db.objects.create(MetaObject(
            company_id=company_id, meta_id=meta_id, kind=kind, launch_id=launch_id, parent_meta_id=parent,
            name=name, status=status, daily_budget=budget, created_at=self._clock.now()))

    def set_registry(self, meta_id: str, **changes: Any) -> None:
        obj = self._db.objects.get_by_meta_id(meta_id)
        if obj is not None:
            self._db.objects.update(replace(obj, **changes))

    def save_cap(self, meta_id: str, cap: Money) -> None:
        """Запоминает защитный лимит кампании (в данных её шага саги)."""
        obj = self._db.objects.get_by_meta_id(meta_id)
        if obj is None or not obj.launch_id:
            return
        for step in self._db.launch_steps.list(launch_id=obj.launch_id, meta_id=meta_id):
            cap_json = {"amount_minor": cap.amount_minor, "currency": cap.currency}
            self._db.launch_steps.update(replace(step, data={**step.data, "spend_cap": cap_json}))
            return
