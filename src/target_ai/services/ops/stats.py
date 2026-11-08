"""Забор статистики из Meta в stats_daily (спека §6 «Статистика»)."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from target_ai.money import from_major
from target_ai.storage import Company, DailyStat

from .common import OpsContext

log = logging.getLogger(__name__)

REFETCH_DAYS = 3  # Meta дообновляет последние дни
FIRST_FETCH_DAYS = 31
RESULT_ACTIONS = (  # «результат» по цели (спека §6, таблица «Цели»); первая найденная
    "onsite_conversion.messaging_conversation_started_7d", "onsite_conversion.lead_grouped", "lead", "link_click",
)


OBJECTIVE_ACTIONS = {  # спека §6, таблица целей
    "direct_messages": ("onsite_conversion.messaging_conversation_started_7d",),
    "lead_form": ("onsite_conversion.lead_grouped", "lead"),
    "website_traffic": ("link_click",),
}


def parse_results(row: dict[str, Any], objective: str | None = None) -> int:
    """Целевые результаты строки /insights: поле `results`, иначе действие по цели."""
    for item in row.get("results") or []:
        for value in item.get("values") or []:
            try:
                return int(Decimal(str(value.get("value", 0))))
            except InvalidOperation:
                continue
    actions = {a.get("action_type"): a.get("value") for a in row.get("actions") or []}
    for name in (*OBJECTIVE_ACTIONS.get(objective or "", ()), *RESULT_ACTIONS):
        if name in actions:
            return int(Decimal(str(actions[name])))
    return 0


def _int(raw: Any) -> int:
    return int(Decimal(str(raw or 0)))


class StatsSync:
    def __init__(self, ctx: OpsContext) -> None:
        self._ctx = ctx

    async def sync_all(self) -> int:
        total = 0
        for company in await self._ctx.active_companies():
            total += await self.sync_company(company.id)
        return total

    async def sync_company(self, company_id: int) -> int:
        """Возвращает число записанных строк; ошибка Meta вылетает — задача сообщит о ней."""
        ctx = self._ctx
        company = await ctx.company(company_id)
        own = {o.meta_id: o for o in await ctx.io(ctx.db.objects.list, company_id=company.id)
               if o.kind in ("adset", "ad")}
        if not own:
            return 0
        since, until = await self._window(company)
        gateway, written = ctx.gateway(), 0
        plan = await ctx.io(ctx.db.plans.latest, company.id)
        objective = plan.data.get("objective") if plan else None
        for level in ("adset", "ad"):
            rows = await gateway.get_insights(company.ad_account_id, level=level, since=since, until=until)
            for row in rows:
                meta_id = row.get(f"{level}_id")
                if meta_id in own:
                    await ctx.io(ctx.db.stats.upsert, self._to_stat(company, meta_id, row, objective))
                    written += 1
        return written

    async def _window(self, company: Company) -> tuple[date, date]:
        ctx = self._ctx
        today = ctx.account_today(company)
        stored = await ctx.io(ctx.db.stats.list_range, company.id, "0001-01-01", today.isoformat())
        if not stored:
            return today - timedelta(days=FIRST_FETCH_DAYS), today
        return today - timedelta(days=REFETCH_DAYS - 1), today

    @staticmethod
    def _to_stat(company: Company, meta_id: str, row: dict[str, Any], objective: str | None) -> DailyStat:
        return DailyStat(
            meta_id=meta_id, company_id=company.id, date=str(row["date_start"]),
            spend=from_major(Decimal(str(row.get("spend", "0"))), company.currency),
            impressions=_int(row.get("impressions")), reach=_int(row.get("reach")),
            frequency=float(row.get("frequency") or 0), clicks=_int(row.get("clicks")),
            results=parse_results(row, objective))
