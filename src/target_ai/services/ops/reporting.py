"""Отчёты (истории 55–57): ежедневная сводка, недельный разбор, отчёт по запросу."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from target_ai.agents import ReportInput, report
from target_ai.llm import LLMError
from target_ai.money import Money, format_ru
from target_ai.storage import Company, HistoryEntry

from .common import OpsContext, fmt, strip_tags
from .history import record_result

log = logging.getLogger(__name__)

KIND_WEEKLY = "weekly_insight"
WEEK = 7


def _cpa(spend_minor: int, results: int, currency: str) -> Money | None:
    return Money(round(Decimal(spend_minor) / results), currency) if results else None


class Reporting:
    def __init__(self, ctx: OpsContext) -> None:
        self._ctx = ctx

    # ------------------------------------------------------------------ публичное
    async def daily_all(self) -> list[str]:
        texts = []
        for company in await self._ctx.active_companies():
            text = await self.daily_text(company.id)
            await self._ctx.emit(text)
            texts.append(text)
        return texts

    async def on_demand(self, company_id: int | None = None) -> str:
        """Отчёт по одной компании или по всем (company_id=None)."""
        companies = ([await self._ctx.company(company_id)] if company_id
                     else await self._ctx.active_companies())
        if not companies:
            return "Компаний пока нет."
        return "\n\n".join([await self.daily_text(c.id) for c in companies])

    async def day_line(self, company_id: int) -> str:
        """Одна строка про вчерашний день компании (для итога перемотки)."""
        ctx = self._ctx
        company = await ctx.company(company_id)
        yesterday = ctx.account_today(company) - timedelta(days=1)
        rows = await self._adset_stats(company, yesterday, yesterday)
        cur = company.currency or "USD"
        spend, results = sum(r.spend.amount_minor for r in rows if r.spend), sum(r.results for r in rows)
        return strip_tags(f"{yesterday.isoformat()} «{company.name}»: расход {format_ru(Money(spend, cur))}, "
                          f"результатов {results}, цена {fmt(_cpa(spend, results, cur))}")

    async def daily_text(self, company_id: int) -> str:
        ctx = self._ctx
        company = await ctx.company(company_id)
        today = ctx.account_today(company)
        yesterday = today - timedelta(days=1)
        adsets = await self._adset_stats(company, today.replace(day=1), today)
        day_rows = [r for r in adsets if r.date == yesterday.isoformat()]
        cur = company.currency or "USD"
        spend_day = sum(r.spend.amount_minor for r in day_rows if r.spend)
        results_day = sum(r.results for r in day_rows)
        spend_month = sum(r.spend.amount_minor for r in adsets if r.spend)
        lines = [f"Сводка — «{company.name}» за {yesterday.isoformat()}:",
                 f"Расход вчера: {format_ru(Money(spend_day, cur))}; с начала месяца: "
                 f"{format_ru(Money(spend_month, cur))}{self._of_limit(company, spend_month)}.",
                 self._results_line(company, results_day, spend_day, cur)]
        changes = await self._changes(company)
        lines.append("Изменения за сутки: " + ("; ".join(changes) if changes else "не было."))
        return strip_tags("\n".join(lines))

    async def weekly(self, company_id: int) -> str:
        ctx = self._ctx
        company = await ctx.company(company_id)
        today = ctx.account_today(company)
        rows = await self._adset_stats(company, today - timedelta(days=2 * WEEK), today)
        cut = (today - timedelta(days=WEEK)).isoformat()
        this, prev = [r for r in rows if r.date >= cut], [r for r in rows if r.date < cut]
        cur = company.currency or "USD"
        text = [f"Недельный разбор — «{company.name}»:", self._week_line("Эта неделя", this, cur),
                self._week_line("Прошлая неделя", prev, cur)]
        text += await self._groups_block(company, this, cur)
        text += await self._model_block(company, this, cur)
        return strip_tags("\n".join(text))

    # ------------------------------------------------------------------ части
    async def _adset_stats(self, company: Company, since: date, until: date):
        ctx = self._ctx
        ids = {o.meta_id for o in await ctx.io(ctx.db.objects.list, company_id=company.id, kind="adset")}
        rows = await ctx.io(ctx.db.stats.list_range, company.id, since.isoformat(), until.isoformat())
        return [r for r in rows if r.meta_id in ids]

    @staticmethod
    def _of_limit(company: Company, spend_month: int) -> str:
        limit = company.monthly_limit
        if limit is None or limit.amount_minor == 0:
            return ""
        return f" из {format_ru(limit)} ({spend_month * 100 // limit.amount_minor}%)"

    @staticmethod
    def _results_line(company: Company, results: int, spend: int, cur: str) -> str:
        cpa, target = _cpa(spend, results, cur), company.target_cpa
        if cpa is None:
            return f"Результатов: {results}; цену результата посчитать нельзя."
        verdict = "" if target is None else (" — лучше целевой" if cpa <= target else " — хуже целевой")
        return f"Результатов: {results}; цена результата {format_ru(cpa)} при целевой {fmt(target)}{verdict}."

    @staticmethod
    def _week_line(title: str, rows, cur: str) -> str:
        spend, results = sum(r.spend.amount_minor for r in rows if r.spend), sum(r.results for r in rows)
        cpa = _cpa(spend, results, cur)
        return f"{title}: расход {format_ru(Money(spend, cur))}, результатов {results}, цена {fmt(cpa)}."

    async def _changes(self, company: Company) -> list[str]:
        ctx, since = self._ctx, self._ctx.clock.now() - timedelta(hours=24)
        entries = await ctx.io(ctx.db.audit.list, company_id=company.id, order_by="-id", limit=100)
        return [e.data["reason"] for e in reversed(entries)
                if e.action.startswith("decision:") and e.data.get("outcome") == "ok" and e.data.get("reason")
                and e.created_at and e.created_at >= since]

    async def _groups_block(self, company: Company, rows, cur: str) -> list[str]:
        ctx = self._ctx
        groups = await ctx.io(ctx.db.objects.list, company_id=company.id, kind="adset")
        lines = ["По группам:"]
        for group in groups:
            mine = [r for r in rows if r.meta_id == group.meta_id]
            spend, results = sum(r.spend.amount_minor for r in mine if r.spend), sum(r.results for r in mine)
            lines.append(f"• «{group.name or group.meta_id}»: расход {format_ru(Money(spend, cur))}, "
                         f"результатов {results}, цена {fmt(_cpa(spend, results, cur))}")
            if group.status == "ACTIVE" and mine:
                await record_result(ctx, company, group.meta_id, "недельный разбор")
        for ad in await ctx.io(ctx.db.objects.list, company_id=company.id, kind="ad"):
            if ad.status == "ACTIVE":
                await record_result(ctx, company, ad.meta_id, "недельный разбор")
        return lines

    async def _model_block(self, company: Company, rows, cur: str) -> list[str]:
        ctx = self._ctx
        if ctx.llm is None:
            return []
        try:
            stats = tuple({"date": r.date, "id": r.meta_id, "spend": fmt(r.spend), "results": r.results} for r in rows)
            history = tuple(f"{h.kind}: {h.outcome} {h.data.get('name', '')}"
                            for h in await ctx.io(ctx.db.history.list, company_id=company.id, order_by="-id", limit=20))
            changes = tuple(await self._changes(company))
            result = await report(ctx.llm, ReportInput(company_name=company.name, period="week", stats=stats,
                                                        changes=changes, history=history,
                                                        target_cpa=fmt(company.target_cpa)))
        except LLMError as exc:
            log.warning("Недельный разбор без модели: %s", exc)
            return []
        await ctx.io(ctx.db.history.create, HistoryEntry(
            company_id=company.id, kind=KIND_WEEKLY, outcome=result.summary,
            data={"insights": list(result.insights), "recommendations": list(result.recommendations)},
            created_at=ctx.clock.now()))
        lines = [result.summary, *(f"{s.title}: {s.text}" for s in result.sections)]
        lines += [f"Вывод: {i}" for i in result.insights] + [f"Рекомендация: {r}" for r in result.recommendations]
        return lines

