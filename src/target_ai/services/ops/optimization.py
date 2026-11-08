"""Оптимизация раз в сутки: темп → правила → предложения модели → Исполнитель (истории 47–54)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from target_ai import policy
from target_ai.executor import ExecutorError, PolicyDenied
from target_ai.llm import LLMError
from target_ai.policy import OptimizerDecision

from .common import OpsContext, strip_tags
from .history import record_result
from target_ai.config.runtime import SETTING_MONTH

log = logging.getLogger(__name__)

CB_APPLY, CB_CANCEL = "ops:apply:", "ops:cancel:"
APPROVAL_KIND = "optimizer_scale"
EXHAUSTED_MARK = "лимит месяца исчерпан"
MODEL_ACTIONS = {"pause_ad": "pause_ad", "pause_group": "pause_group"}


@dataclass(frozen=True)
class OptimizationResult:
    company_id: int
    lines: tuple[str, ...] = ()  # изменения, каждое со своими цифрами
    warnings: tuple[str, ...] = ()
    approval_id: str | None = None
    skipped_reason: str | None = None
    buttons: list = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.lines)


class Optimization:
    def __init__(self, ctx: OpsContext) -> None:
        self._ctx = ctx

    async def run_all(self) -> list[OptimizationResult]:
        return [await self.run_daily(c.id) for c in await self._ctx.active_companies()]

    async def run_daily(self, company_id: int) -> OptimizationResult:
        notes = await self._rollover(company_id)
        result = await self._run(company_id, None)
        return await self._announce(result, notes)

    async def apply_approved(self, company_id: int, approval_id: str) -> OptimizationResult:
        """Кнопка «Применить»: решения пересчитываются по свежему снимку, ключи сверяет политика."""
        return await self._announce(await self._run(company_id, approval_id), [])

    async def cancel(self, approval_id: str) -> bool:
        return await self._ctx.io(self._ctx.db.approvals.cancel, approval_id)

    # ------------------------------------------------------------------ внутреннее
    async def _rollover(self, company_id: int) -> list[str]:
        ctx = self._ctx
        company = await ctx.company(company_id)
        month = ctx.account_today(company).strftime("%Y-%m")
        key = SETTING_MONTH.format(company_id)
        previous = await ctx.io(ctx.db.settings.get, key)
        await ctx.io(ctx.db.settings.set, key, month)
        if previous is None or previous == month:
            return []
        try:
            done = await ctx.executor.month_rollover(company_id)
        except (PolicyDenied, ExecutorError) as exc:
            return [strip_tags(f"Новый месяц: защитные лимиты не обновлены — {exc}")]
        return [strip_tags(x) for x in (f"Новый месяц {month}: {done.message_ru}", *done.details)]

    async def _decisions(self, company, snapshot) -> list[OptimizerDecision]:
        pace = policy.enforce_pace(snapshot)
        decided = policy.decide(snapshot, target_cpa=company.target_cpa) if company.target_cpa else []
        taken = {d.target_id for d in pace}
        rules = [d for d in decided if d.target_id not in taken or d.kind == "fatigue_warning"]
        return [*pace, *rules, *await self._model_suggestions(company, snapshot, [*pace, *rules])]

    async def _model_suggestions(self, company, snapshot, base) -> list[OptimizerDecision]:
        from target_ai.agents import DecisionInfo, ExplainInput, explain

        if self._ctx.llm is None or not base:
            return []
        info = tuple(DecisionInfo(index=i, kind=d.kind, target=d.target_id, reason=d.reason_ru)
                     for i, d in enumerate(base))
        try:
            output = await explain(self._ctx.llm, ExplainInput(company_name=company.name, decisions=info))
        except LLMError as exc:
            log.warning("Предложения модели недоступны: %s", exc)
            return []
        known = {g.id for g in snapshot.groups} | {a.id for g in snapshot.groups for a in g.ads}
        taken = {d.target_id for d in base}
        return [OptimizerDecision(MODEL_ACTIONS[s.action], s.target, None, f"Предложение модели: {s.reason}")  # type: ignore[arg-type]
                for s in output.suggestions if s.action in MODEL_ACTIONS and s.target in known - taken]

    async def _run(self, company_id: int, approval_id: str | None) -> OptimizationResult:
        ctx = self._ctx
        company = await ctx.company(company_id)
        snapshot = await ctx.executor.build_snapshot(company_id)
        if snapshot.halted or company.status != "active":
            return OptimizationResult(company_id, skipped_reason="стоп-кран или пауза компании")
        decisions = await self._decisions(company, snapshot)
        warnings = tuple(d.reason_ru for d in decisions if d.kind == "fatigue_warning")
        actionable = [d for d in decisions if d.kind != "fatigue_warning"]
        if not actionable:
            return OptimizationResult(company_id, warnings=await self._fresh(company, warnings))
        report = await ctx.executor.apply_decisions(company_id, actionable, approval_id, snapshot=snapshot)
        lines, pending = [], []
        for item in report.applied:
            if item.status == "applied":
                lines.append(item.message_ru)
                if item.kind in ("pause_group", "pause_ad"):
                    await record_result(ctx, company, item.target_id, item.message_ru)
            elif item.status == "needs_approval":
                pending.append(item)
            elif item.status in ("denied", "failed"):
                lines.append(f"Не применено: {item.message_ru}")
        approval, buttons = await self._offer(company_id, actionable, pending, lines)
        lines = self._collapse_exhausted(company, lines)
        return OptimizationResult(company_id, tuple(strip_tags(x) for x in lines), await self._fresh(company, warnings), approval,
                                  None, buttons)

    def _collapse_exhausted(self, company, lines: list[str]) -> list[str]:
        """Остановка по исчерпанию лимита — одно сообщение, а не по строке на каждую группу."""
        rest = [x for x in lines if EXHAUSTED_MARK not in x]
        if len(rest) == len(lines):
            return lines
        today = self._ctx.account_today(company)
        first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        return [*rest, f"Лимит месяца исчерпан — кампании на паузе до {first_next:%d.%m} или до увеличения лимита."]

    async def _offer(self, company_id, decisions, pending, lines):
        if not pending:
            return None, []
        ctx = self._ctx
        reasons = {(d.kind, d.target_id): d.reason_ru for d in decisions}
        for old in await ctx.io(ctx.db.approvals.list, kind=APPROVAL_KIND, company_id=company_id, status="pending"):
            await ctx.io(ctx.db.approvals.cancel, old.id)  # новое предложение заменяет прежнее
        keys = [p.needs_approval.key for p in pending]
        lines.extend(f"Предложение (нужна кнопка): {reasons.get((p.kind, p.target_id), p.message_ru)}" for p in pending)
        approval = await ctx.io(ctx.db.approvals.create, kind=APPROVAL_KIND, now=ctx.clock.now(), company_id=company_id,
                                payload={"keys": keys, "summary": pending[0].message_ru})
        return approval.id, [[("Применить", CB_APPLY + approval.id), ("Отмена", CB_CANCEL + approval.id)]]

    async def _fresh(self, company, warnings: tuple[str, ...]) -> tuple[str, ...]:
        """Предупреждение о выгорании — не чаще раза в сутки на группу."""
        ctx, shown = self._ctx, []
        for text in warnings:
            if await ctx.io(ctx.db.alerts.mark, f"fatigue:{company.id}:{text[:40]}:{ctx.account_today(company)}", ctx.clock.now()):
                shown.append(strip_tags(text))
        return tuple(shown)

    async def _announce(self, result: OptimizationResult, notes: list[str]) -> OptimizationResult:
        body = [*notes, *(f"• {x}" for x in result.lines), *(f"⚠️ {w}" for w in result.warnings)]
        if body:
            company = await self._ctx.company(result.company_id)
            await self._ctx.emit(f"Оптимизация — «{company.name}»:\n" + "\n".join(body), result.buttons)
        return result
