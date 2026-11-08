"""История компании: итог по группе/объявлению (гипотеза, дни, расход, результаты, цена, вердикт)."""

from __future__ import annotations

from decimal import Decimal

from target_ai.money import Money, format_ru
from target_ai.storage import Company, HistoryEntry, MetaObject

from .common import OpsContext

KIND_GROUP, KIND_AD = "group_result", "ad_result"
STRONG, WEAK, NORMAL, NO_RESULTS = "сильная", "слабая", "в норме", "нет результатов"
WEAK_MULTIPLE = Decimal(2)


def verdict(spend_minor: int, results: int, target: Money | None) -> str:
    if results == 0:
        return NO_RESULTS
    if target is None or target.amount_minor <= 0:
        return NORMAL
    cpa = Decimal(spend_minor) / results
    if cpa <= target.amount_minor:
        return STRONG
    return WEAK if cpa >= target.amount_minor * WEAK_MULTIPLE else NORMAL


def _hypothesis(ctx: OpsContext, obj: MetaObject) -> str | None:
    group = obj if obj.kind == "adset" else ctx.db.objects.get_by_meta_id(obj.parent_meta_id or "")
    launch = ctx.db.launches.get(group.launch_id) if group and group.launch_id else None
    plan = ctx.db.plans.get(launch.plan_id) if launch else None
    for item in (plan.data.get("groups", []) if plan else []):
        if item.get("name") and item["name"] in group.name:
            return item.get("hypothesis")
    return None


def _write(ctx: OpsContext, company: Company, meta_id: str, why: str) -> HistoryEntry | None:
    obj = ctx.db.objects.get_by_meta_id(meta_id)
    if obj is None or obj.kind not in ("adset", "ad"):
        return None
    rows = ctx.db.stats.list(meta_id=meta_id)
    cur = company.currency or "USD"
    spend = sum(r.spend.amount_minor for r in rows if r.spend)
    results = sum(r.results for r in rows)
    outcome = verdict(spend, results, company.target_cpa)
    cpa = Money(round(Decimal(spend) / results), cur) if results else None
    data = {"meta_id": meta_id, "name": obj.name, "days": len(rows), "spend": format_ru(Money(spend, cur)),
            "results": results, "cpa": format_ru(cpa) if cpa else None, "why": why,
            "audience": obj.name, "creative": obj.name if obj.kind == "ad" else None}
    return ctx.db.history.create(HistoryEntry(
        company_id=company.id, kind=KIND_GROUP if obj.kind == "adset" else KIND_AD,
        hypothesis=_hypothesis(ctx, obj), outcome=outcome, data=data, created_at=ctx.clock.now()))


async def record_result(ctx: OpsContext, company: Company, meta_id: str, why: str) -> HistoryEntry | None:
    return await ctx.io(_write, ctx, company, meta_id, why)
