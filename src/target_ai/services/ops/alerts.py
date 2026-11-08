"""Мгновенные уведомления (история 58): опрос раз в 15 минут, одно сообщение на событие в сутки."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from target_ai.meta.errors import MetaError
from target_ai.money import Money, format_ru
from target_ai.storage import Company

from .common import OpsContext, comma, strip_tags

log = logging.getLogger(__name__)

ACCOUNT_OK = 1
RESTRICTED = frozenset({2, 7, 100, 101})
PAYMENT = frozenset({3, 8, 9})
CPA_HISTORY_DAYS = 7


class Alerts:
    def __init__(self, ctx: OpsContext) -> None:
        self._ctx = ctx

    async def check_all(self) -> list[str]:
        sent: list[str] = []
        for company in await self._ctx.active_companies():
            sent += await self.check_company(company.id)
        return sent

    async def check_company(self, company_id: int) -> list[str]:
        """Возвращает тексты реально отправленных уведомлений."""
        company = await self._ctx.company(company_id)
        found = [*await self._account(company), *await self._no_impressions(company),
                 *await self._spend_and_cpa(company)]
        return [text for key, text in found if await self._send_once(company, key, text)]

    async def check_token(self) -> list[str]:
        """Токен: недействителен / истекает за `token_days`. Раз в 6 ч и при ошибке 190."""
        ctx, sent = self._ctx, []
        now = ctx.clock.now()
        try:
            info = await ctx.gateway().debug_token()
        except MetaError as exc:
            return await self._once(f"token:error:{now.date()}", f"Токен Meta: {exc.user_message_ru}")
        if not info.is_valid:
            sent += await self._once(f"token:expired:{now.date()}",
                                     "Токен Meta недействителен или истёк. Выпустите новый и обновите META_ACCESS_TOKEN.")
        elif info.expiry_known and info.expires_at is not None:
            days = (info.expires_at - now).days
            if days <= ctx.thresholds().token_days:
                sent += await self._once(f"token:soon:{now.date()}",
                                         f"Токен Meta истекает через {days} дн. Выпустите новый заранее.")
        return sent

    async def _once(self, key: str, text: str) -> list[str]:
        ctx = self._ctx
        if await ctx.io(ctx.db.alerts.mark, key, ctx.clock.now()):
            shown = strip_tags(text)
            await ctx.emit(shown)
            return [shown]
        return []

    async def _send_once(self, company: Company, key: str, text: str) -> bool:
        return bool(await self._once(f"{key}:{self._ctx.account_today(company)}", text))

    async def _account(self, company: Company) -> list[tuple[str, str]]:
        try:
            account = await self._ctx.gateway().get_ad_account(company.ad_account_id)
        except MetaError as exc:
            return [(f"meta:{company.id}:{exc.kind}", f"«{company.name}»: {exc.user_message_ru}")]
        found = []
        if account.account_status in PAYMENT or (account.account_status == ACCOUNT_OK and not account.funding_source):
            found.append((f"payment:{company.id}", f"«{company.name}»: проблема с оплатой рекламного аккаунта — "
                          "нет способа оплаты, объявления не будут показываться."))
        elif account.account_status != ACCOUNT_OK:
            found.append((f"restricted:{company.id}", f"«{company.name}»: рекламный аккаунт ограничен "
                          f"(статус {account.account_status}, причина {account.disable_reason})."))
        return found

    async def _no_impressions(self, company: Company) -> list[tuple[str, str]]:
        ctx = self._ctx
        hours = ctx.thresholds().no_impressions_hours
        snapshot = await ctx.executor.build_snapshot(company.id)
        found = []
        for group in snapshot.groups:
            since = group.active_since
            if not snapshot.is_spending(group) or since is None or snapshot.now - since < timedelta(hours=hours):
                continue
            if sum(ad.impressions for ad in group.ads) == 0:
                found.append((f"no_impressions:{group.id}", f"«{company.name}»: группа «{group.name or group.id}» "
                              f"активна {hours}+ ч, но показов нет."))
        return found

    async def _spend_and_cpa(self, company: Company) -> list[tuple[str, str]]:
        ctx, limits = self._ctx, self._ctx.thresholds()
        groups = [o for o in await ctx.io(ctx.db.objects.list, company_id=company.id, kind="adset")
                  if o.status == "ACTIVE"]
        found = []
        for group in groups:
            rows = await ctx.io(ctx.db.stats.list, meta_id=group.meta_id, order_by="date")
            if not rows:
                continue
            last, history = rows[-1], rows[-1 - CPA_HISTORY_DAYS:-1]
            title = f"«{company.name}», группа «{group.name or group.meta_id}»"
            budget = group.daily_budget
            if budget and last.spend and last.spend.amount_minor > budget.amount_minor * limits.spend_spike_ratio:
                found.append((f"spend:{group.meta_id}", f"{title}: расход за {last.date} "
                              f"{format_ru(last.spend)} — больше {comma(limits.spend_spike_ratio)}× дневного бюджета "
                              f"{format_ru(budget)}."))
            alert = self._cpa_alert(title, last, history, limits.cpa_spike_ratio, company.currency)
            if alert:
                found.append((f"cpa:{group.meta_id}", alert))
        return found

    @staticmethod
    def _cpa_alert(title, last, history, ratio: Decimal, currency) -> str | None:
        spend = sum(r.spend.amount_minor for r in history if r.spend)
        results = sum(r.results for r in history)
        if not results or not last.results or not last.spend:
            return None
        average, today = Decimal(spend) / results, Decimal(last.spend.amount_minor) / last.results
        if today <= average * ratio:
            return None
        return (f"{title}: цена результата за {last.date} {format_ru(Money(round(today), currency))} — "
                f"больше {comma(ratio)}× средней за {CPA_HISTORY_DAYS} дн. ({format_ru(Money(round(average), currency))}).")
