"""Стоп-кран, пауза и возобновление, пакеты решений, лимиты, исправление объявления."""

from __future__ import annotations

import logging
from dataclasses import replace

from target_ai.meta.errors import MetaError
from target_ai.money import Money, format_ru
from target_ai.policy import (
    Activate, Allowed, CampaignState, Delete, Denied, NeedsApproval, OptimizerDecision, Pause, SetDailyBudget,
    SetMonthlyLimit, SetSpendCap,
)
from target_ai.storage import MetaObject

from .core import DEAD_STATUSES, RETIRED
from .errors import ApprovalInvalid, ApprovalRequired, ExecutorError, PolicyDenied, StepFailed
from .results import AppliedDecision, ChangeResult, DecisionsReport, PauseReport
from .spec import parse_spec
from .validation import Validation
from target_ai.config.runtime import SETTING_HALTED

log = logging.getLogger(__name__)


class HaltRolledBack(PolicyDenied):
    """Стоп-кран поднялся во время запроса, и изменение уже откатано (отказ до вызова — обычный `PolicyDenied`)."""


SHRINK_KINDS = ("pause_group", "pause_ad")


class Controls(Validation):
    # ------------------------------------------------------------------ пауза
    async def halt_all(self, *, actor: str = "user") -> PauseReport:
        """Сначала флаг, потом паузы; ошибка на одной кампании не прерывает остальные."""
        await self.io(self._db.settings.set, SETTING_HALTED, True)
        await self.audit(actor, "halt_all", None, "ok")
        campaigns = await self.io(self._all_campaigns, None)  # реестр мог отстать от Meta: паузим все, не только ACTIVE
        report = await self._pause_campaigns(campaigns, actor, "Стоп-кран")
        if report.failed:
            await self.notify(report.message_ru)
        return report

    async def pause_company(self, company_id: int, *, actor: str = "user") -> PauseReport:
        campaigns = await self.io(self._all_campaigns, company_id)
        return await self._pause_campaigns(campaigns, actor, "Пауза компании")

    def _all_campaigns(self, company_id: int | None) -> list[MetaObject]:
        rows = self._db.objects.list(kind="campaign") if company_id is None \
            else self._db.objects.list(company_id=company_id, kind="campaign")
        return [o for o in rows if o.status not in DEAD_STATUSES]

    async def _confirm_paused(self, meta_id: str) -> bool:
        """Сверка с Meta после сбоя запроса паузы: пауза могла пройти, а ответ потеряться."""
        try:
            return (await self.gateway().get_ad_status(meta_id)).get("status") == "PAUSED"
        except Exception:
            return False

    async def _pause_campaigns(self, campaigns: list[MetaObject], actor: str, title: str) -> PauseReport:
        paused: list[str] = []
        failed: list[tuple[str, str]] = []
        for campaign in campaigns:
            try:
                await self.guard(Pause(campaign.meta_id), campaign.company_id, actor=actor, safety=True)
                try:
                    await self.mutate(actor, campaign.company_id, "pause", {"campaign": campaign.meta_id},
                                      lambda c=campaign: self.gateway().set_status(c.meta_id, "PAUSED"))
                except MetaError:
                    if not await self._confirm_paused(campaign.meta_id):
                        raise
                if campaign.status != RETIRED:
                    await self.io(self.set_registry, campaign.meta_id, status="PAUSED")
                paused.append(campaign.meta_id)
            except (MetaError, PolicyDenied, ExecutorError) as exc:
                failed.append((campaign.meta_id, getattr(exc, "user_message_ru", None) or str(exc)))
            except Exception as exc:  # одна кампания не должна остановить остальные
                log.exception("Пауза кампании %s не удалась", campaign.meta_id)
                failed.append((campaign.meta_id, str(exc)))
        text = f"{title}: остановлено {len(paused)} кампаний."
        if failed:
            text += " НЕ остановлены: " + "; ".join(f"{cid} ({why})" for cid, why in failed) + "."
        return PauseReport(tuple(paused), tuple(failed), text)

    async def resume_system(self, approval_id: str, *, actor: str = "user") -> ChangeResult:
        """Снимает флаг по подтверждению; ничего не включает."""
        if not self.peek_keys_any(approval_id, "resume_system"):
            raise ApprovalInvalid("Подтверждение «Возобновить систему» не найдено, использовано или просрочено.")
        await self.io(self.consume, approval_id)
        await self.io(self._db.settings.set, SETTING_HALTED, False)
        await self.audit(actor, "resume_system", None, "ok", approval_id=approval_id)
        return ChangeResult("done", "Стоп-кран снят. Ничего не включено: возобновляйте компании по одной.")

    def peek_keys_any(self, approval_id: str, kind: str) -> bool:
        approval = self._db.approvals.get(approval_id)
        return bool(approval and approval.status == "pending" and approval.kind == kind
                    and approval.expires_at > self._clock.now())

    async def resume_company(self, company_id: int, approval_id: str | None = None, *, actor: str = "user") -> ChangeResult:
        campaign = await self.io(self._current_paused_campaign, company_id)
        if campaign is None:
            raise ExecutorError("У компании нет приостановленной кампании, которую можно возобновить.")
        snapshot = await self.snapshot(company_id)
        pace = self._policy.enforce_pace(self._as_resumed(snapshot, campaign.meta_id))
        if pace:  # темп пересчитан: сначала снижение до потолка
            await self.apply_decisions(company_id, pace, snapshot=self._as_resumed(snapshot, campaign.meta_id), actor="system")
        keys = await self.io(self.peek_keys, approval_id, company_id)
        try:
            await self.guard(Activate(campaign.meta_id, "campaign"), company_id, actor=actor, approved=keys)
        except ApprovalRequired as need:
            return ChangeResult("needs_approval", need.needs.summary_ru, need.needs)
        await self.io(self.consume, approval_id)
        await self.enable_object(actor, company_id, campaign.meta_id, "resume")
        return ChangeResult("done", "Кампания возобновлена с пересчитанным темпом.")

    def _current_paused_campaign(self, company_id: int) -> MetaObject | None:
        """Текущая кампания — последнего успешного запуска; снятые (`RETIRED`) возвращать нельзя."""
        current = self._current_launch_id(company_id)
        if current is None:
            return None
        return next((o for o in self._db.objects.list(company_id=company_id, kind="campaign", launch_id=current)
                     if o.status == "PAUSED"), None)

    def _current_launch_id(self, company_id: int) -> int | None:
        launched = [x.id for x in self._db.launches.list() if x.company_id == company_id and x.status == "launched"]
        return max(launched, default=None)

    def _current_campaign_id(self, company_id: int) -> str | None:
        current = self._current_launch_id(company_id)
        if current is None:
            return None
        return next((o.meta_id for o in self._db.objects.list(company_id=company_id, kind="campaign",
                                                              launch_id=current) if o.status not in DEAD_STATUSES), None)

    @staticmethod
    def _as_resumed(snapshot, campaign_id: str):
        campaigns = tuple(replace(c, active=True) if c.id == campaign_id else c for c in snapshot.campaigns)
        return replace(snapshot, campaigns=campaigns)

    async def _after_rollback(self, launch) -> None:
        """Откат нового запуска возвращает предыдущую кампанию в «текущие»: пауза, `spend_cap` по правилу."""
        previous = await self.io(self._latest_retired, launch.company_id)
        if previous is None:
            return
        await self.io(self.set_registry, previous.meta_id, status="PAUSED")
        try:
            _notes, failure = await self._refresh_caps_safely(launch.company_id, "system")
        except PolicyDenied as exc:
            failure = exc.reason_ru
        if failure:
            await self.notify(f"Предыдущая кампания возвращена, но её защитный лимит не пересчитан: {failure}")

    def _latest_retired(self, company_id: int) -> MetaObject | None:
        retired = []
        for obj in self._db.objects.list(company_id=company_id, kind="campaign", status=RETIRED):
            launch = self._db.launches.get(obj.launch_id) if obj.launch_id else None
            if launch is not None and launch.status == "launched":
                retired.append(obj)
        return max(retired, key=lambda o: o.launch_id or 0, default=None)

    # ------------------------------------------------------------------ решения оптимизатора
    async def apply_decisions(self, company_id: int, decisions: list[OptimizerDecision],
                              approval_id: str | None = None, *, snapshot=None, actor: str = "optimizer") -> DecisionsReport:
        """Исполняет пакет строго в порядке `check_decisions`; не прошла пауза или снижение — пакет стоп."""
        snap = snapshot or await self.snapshot(company_id)
        keys = await self.io(self.peek_keys, approval_id, company_id)
        verdicts = self._policy.check_decisions(decisions, snap, approved_keys=keys)
        await self.io(self.consume, approval_id)
        applied: list[AppliedDecision] = []
        stopped = False
        budgets = {g.id: g.daily_budget for g in snap.groups}
        for decision, verdict in verdicts:
            if stopped:
                applied.append(AppliedDecision(decision.target_id, decision.kind, "skipped", "Пакет остановлен."))
                continue
            item = await self._apply_one(company_id, decision, verdict, actor, budgets.get(decision.target_id))
            applied.append(item)
            stopped = item.status != "applied" and self._is_shrinking(decision)
        done = sum(1 for a in applied if a.status == "applied")
        text = f"Применено решений: {done} из {len(applied)}." + (" Пакет остановлен." if stopped else "")
        return DecisionsReport(tuple(applied), stopped, text)

    @staticmethod
    def _is_shrinking(decision: OptimizerDecision) -> bool:
        return decision.kind in SHRINK_KINDS or (decision.kind == "set_budget" and decision.lowers_for_limit)

    async def _apply_one(self, company_id: int, decision: OptimizerDecision, verdict, actor: str,
                         previous: Money | None = None) -> AppliedDecision:
        target, kind = decision.target_id, decision.kind
        if isinstance(verdict, Denied):
            await self.audit(actor, f"decision:{kind}", company_id, "denied", target=target, code=verdict.code)
            return AppliedDecision(target, kind, "denied", verdict.reason_ru)
        if isinstance(verdict, NeedsApproval):
            return AppliedDecision(target, kind, "needs_approval", verdict.summary_ru, verdict)
        try:
            if not self._is_shrinking(decision):
                self.refuse_if_halted()  # рост бюджета: флаг перечитывается прямо перед вызовом Meta
            if kind in SHRINK_KINDS:
                await self.mutate(actor, company_id, "decision:pause", {"target": target, "reason": decision.reason_ru},
                                  lambda: self.gateway().set_status(target, "PAUSED"))
                await self.io(self.set_registry, target, status="PAUSED")
            else:
                budget = decision.new_budget
                await self.mutate(actor, company_id, f"decision:{kind}", {"target": target, "reason": decision.reason_ru},
                                  lambda: self.gateway().set_daily_budget(target, budget))
                await self.io(self.set_registry, target, daily_budget=budget,
                              last_budget_change_at=self._clock.now())
                if not self._is_shrinking(decision) and self.is_halted():  # стоп-кран поднят, пока шёл запрос
                    await self._revert_budget(actor, company_id, target, previous)
                    raise PolicyDenied("Стоп-кран включён во время запроса: бюджет возвращён.", "halted")
        except PolicyDenied as exc:
            return AppliedDecision(target, kind, "denied", exc.reason_ru)
        except MetaError as exc:
            return AppliedDecision(target, kind, "failed", exc.user_message_ru)
        return AppliedDecision(target, kind, "applied", decision.reason_ru)

    async def _revert_budget(self, actor: str, company_id: int, target: str, previous: Money | None) -> None:
        if previous is None:
            log.error("Стоп-кран поднят во время роста бюджета %s, прежний бюджет неизвестен", target)
            return
        try:
            await self.mutate("system", company_id, "decision:rollback_halt", {"target": target, "actor": actor},
                              lambda: self.gateway().set_daily_budget(target, previous))
            await self.io(self.set_registry, target, daily_budget=previous)
        except Exception:
            log.exception("Не удалось вернуть бюджет %s после стоп-крана", target)

    # ------------------------------------------------------------------ лимиты
    async def set_monthly_limit(self, company_id: int, new_limit: Money, approval_id: str | None = None,
                                *, actor: str = "user") -> ChangeResult:
        """Снижение — сразу и без кнопки: сначала защитные лимиты в Meta, потом сохранение; рост — по подтверждению."""
        keys = await self.io(self.peek_keys, approval_id, company_id)
        try:
            await self.guard(SetMonthlyLimit(new_limit), company_id, actor=actor, approved=keys)
        except ApprovalRequired as need:
            return ChangeResult("needs_approval", need.needs.summary_ru, need.needs)
        company = await self.io(self._db.companies.get, company_id)
        lowering = company.monthly_limit is not None and new_limit.amount_minor < company.monthly_limit.amount_minor
        notes: list[str] = []
        if lowering:  # Meta первой: не вышло — лимит не меняется
            notes, failure = await self._refresh_caps_safely(company_id, actor, limit=new_limit)
            if failure:
                return ChangeResult("failed", f"Месячный лимит не изменён: {failure}")
        await self.io(self.consume, approval_id)
        await self.io(self._db.companies.update, replace(company, monthly_limit=new_limit))
        await self.audit(actor, "set_monthly_limit", company_id, "ok", new_limit=format_ru(new_limit))
        if not lowering:
            notes, failure = await self._refresh_caps_safely(company_id, actor)
        snap = await self.snapshot(company_id)
        pace = self._policy.enforce_pace(snap)
        if pace:
            await self.apply_decisions(company_id, pace, snapshot=snap, actor="system")
        if failure:
            return ChangeResult("partial", f"Месячный лимит сохранён, но защитный лимит в Meta не обновлён: {failure}",
                                details=tuple(notes))
        return ChangeResult("done", f"Месячный лимит: {format_ru(new_limit)}.", details=tuple(notes))

    async def month_rollover(self, company_id: int, approval_id: str | None = None, *, actor: str = "system") -> ChangeResult:
        """Новый месяц: защитный лимит текущей кампании = расход + новый остаток (в пределах лимита — без кнопки)."""
        try:
            notes, failure = await self._refresh_caps_safely(company_id, actor)
        except HaltRolledBack as exc:  # стоп-кран поднялся во время запроса: лимит уже возвращён
            return ChangeResult("failed", f"Защитные лимиты не обновлены: {exc.reason_ru}")
        if failure:
            return ChangeResult("failed", f"Защитные лимиты не обновлены: {failure}", details=tuple(notes))
        await self.io(self.consume, approval_id)
        return ChangeResult("done", "Новый месяц: защитные лимиты обновлены.", details=tuple(notes))

    async def _refresh_caps_safely(self, company_id: int, actor: str, *, limit: Money | None = None
                                   ) -> tuple[list[str], str | None]:
        """Ошибка Meta не вылетает исключением: возвращается текстом для пользователя. Отказ политики — вылетает."""
        notes: list[str] = []
        try:
            await self._refresh_caps(company_id, actor, notes, limit)
        except MetaError as exc:
            return notes, exc.user_message_ru
        return notes, None

    async def _refresh_caps(self, company_id: int, actor: str, notes: list[str], limit: Money | None) -> None:
        snap = await self.snapshot(company_id, limit=limit)
        current = await self.io(self._current_campaign_id, company_id) or next(
            (c.id for c in snap.campaigns if c.active), snap.campaigns[-1].id if snap.campaigns else None)
        for campaign in snap.campaigns:
            # текущая: расход + остаток; любая другая (снятая): ровно то, что уже потрачено
            target = campaign.spent + snap.remaining if campaign.id == current else campaign.spent
            if snap.min_spend_cap is not None and target < snap.min_spend_cap:
                target = snap.min_spend_cap
            if target.amount_minor <= 0 or campaign.spend_cap == target:
                continue
            if campaign.id != current and campaign.spend_cap is not None and campaign.spend_cap <= target:
                continue
            await self.guard(SetSpendCap(campaign.id, target), company_id, actor=actor, self_approve=True)
            raising = campaign.spend_cap is None or target > campaign.spend_cap

            async def call(c=campaign, t=target, up=raising) -> None:
                if up:
                    self.refuse_if_halted()
                await self.gateway().set_spend_cap(c.id, t)

            await self.mutate(actor, company_id, "spend_cap", {"campaign": campaign.id, "cap": target.amount_minor}, call)
            await self.io(self.save_cap, campaign.id, target)
            if raising and self.is_halted():  # стоп-кран поднят, пока шёл запрос: лимит назад
                await self._revert_cap(company_id, campaign)
                raise HaltRolledBack("Стоп-кран включён во время запроса: защитный лимит возвращён.", "halted")
            notes.append(f"{campaign.id}: защитный лимит {format_ru(target)}")

    async def _revert_cap(self, company_id: int, campaign) -> None:
        if campaign.spend_cap is None:  # лимит появился там, где его не было: расход он не повышает
            return
        try:
            await self.mutate("system", company_id, "spend_cap", {"campaign": campaign.id, "cap": campaign.spend_cap.amount_minor},
                              lambda: self.gateway().set_spend_cap(campaign.id, campaign.spend_cap))
            await self.io(self.save_cap, campaign.id, campaign.spend_cap)
        except Exception:
            log.exception("Не удалось вернуть защитный лимит %s после стоп-крана", campaign.id)

    # ------------------------------------------------------------------ исправление отклонённого
    async def apply_creative_fix(self, ad_id: str, copy, approval_id: str | None = None, *, actor: str = "user") -> ChangeResult:
        """Новое объявление в той же группе, затем удаление отклонённого (спека §6 «Модерация»)."""
        old = await self.io(self._db.objects.get_by_meta_id, ad_id)
        if old is None or old.kind != "ad" or not old.launch_id:
            raise PolicyDenied(f"Объявление {ad_id} создано не этой системой: чужое не трогаю.", "not_own")
        company = await self.io(self._db.companies.get, old.company_id)
        if company.mode == "confirm" and not self.peek_keys_any(approval_id or "", "creative_fix"):
            need = NeedsApproval("creative_fix", "Применить исправленный текст объявления: старое будет заменено новым.")
            return ChangeResult("needs_approval", need.summary_ru, need)
        await self.io(self.consume, approval_id)
        launch = await self.io(self._db.launches.get, old.launch_id)
        plan = await self.io(self._db.plans.get, launch.plan_id)
        run = await self._make_run(launch, plan, parse_spec(plan.data), actor, create=True)
        params = await self.io(self._fixed_creative_params, old, copy)
        creative = await self._fix_step(run, f"fix:{ad_id}:creative", "creative", None, "create_creative", params)
        new_ad = await self._fix_step(run, f"fix:{ad_id}:ad", "ad", old.parent_meta_id, "create_ad", {
            "adset_id": old.parent_meta_id, "creative": {"creative_id": creative}, "status": "PAUSED"})
        if old.status == "ACTIVE":
            await self.guard(Activate(new_ad, "ad"), company.id, actor=actor)
            await self.enable_object(actor, company.id, new_ad, "fix:activate")
        await self.guard(Delete(ad_id), company.id, actor=actor)
        await self.mutate(actor, company.id, "fix:delete_old", {"ad": ad_id}, lambda: self.gateway().delete(ad_id))
        await self.io(self.set_registry, ad_id, status="DELETED")
        return ChangeResult("done", "Исправленное объявление создано, отклонённое удалено.", details=(new_ad,))

    async def _fix_step(self, run, step: str, kind: str, parent: str | None, method: str, params: dict) -> str:
        from target_ai.policy import CreateObject

        name = run.name(f"{run.spec.name} · исправление", step)
        found = await self._ensure(
            run, step, kind=kind, parent=parent, method=method, action=CreateObject(kind, parent),  # type: ignore[arg-type]
            lookup=(parent, "ads") if kind == "ad" else (run.account, "adcreatives"),
            make_params=lambda: {**params, "name": name})
        if not found:
            raise StepFailed(step, "Не удалось создать объект исправления.")
        return found

    def _fixed_creative_params(self, old: MetaObject, copy) -> dict:
        steps = self._db.launch_steps.list(launch_id=old.launch_id)
        ad_step = next(s for s in steps if s.meta_id == old.meta_id)
        creative_step = next(s for s in steps if s.step == "creative:" + ad_step.step.split(":", 1)[1])
        params = {k: v for k, v in (creative_step.data.get("params") or {}).items() if k != "name"}
        photo = (params.get("object_story_spec") or {}).get("photo_data")
        if photo is None:
            raise ExecutorError("Подпись существующего поста изменить нельзя: исправление возможно только для своих креативов.")
        photo["caption"], photo["name"] = copy.primary_text, copy.headline
        return params
