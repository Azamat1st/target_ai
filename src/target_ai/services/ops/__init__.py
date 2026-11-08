"""Сервисы «после запуска»: статистика, оптимизация, отчёты, уведомления, модерация, перемотка демо."""

from __future__ import annotations

from target_ai.meta.errors import TOKEN_EXPIRED, TOKEN_INVALID, MetaError
from target_ai.scheduler import schedule as sch

from .alerts import Alerts
from .common import OpsContext, Thresholds
from .demo import DemoResult, DemoTime
from .moderation import Moderation
from .optimization import Optimization, OptimizationResult
from .reporting import Reporting
from .stats import StatsSync


TOKEN_KINDS = (TOKEN_EXPIRED, TOKEN_INVALID)


class Ops:
    """Связывает сервисы и отдаёт обработчики для `Scheduler`."""

    def __init__(self, ctx: OpsContext) -> None:
        self.ctx = ctx
        self.stats, self.alerts, self.moderation = StatsSync(ctx), Alerts(ctx), Moderation(ctx)
        self.optimization, self.reporting = Optimization(ctx), Reporting(ctx)

    def _guarded(self, handler):
        """Ошибка токена (190) в любой задаче сразу запускает проверку токена вместо общего сбоя."""
        async def run(company_id: int):
            try:
                return await handler(company_id)
            except MetaError as exc:
                if exc.kind not in TOKEN_KINDS:
                    raise
                return await self.alerts.check_token()
        return run

    def handlers(self) -> dict:
        guard = self._guarded
        return {
            sch.STATS: guard(self.stats.sync_company),
            sch.MODERATION: guard(self.moderation.check_company),
            sch.ALERTS: guard(self.alerts.check_company),
            sch.TOKEN: lambda _company_id: self.alerts.check_token(),
            sch.OPTIMIZE: self.optimization.run_daily,
            sch.DAILY_REPORT: self._daily_report,
            sch.WEEKLY_REPORT: self._weekly_report,
        }

    async def _daily_report(self, company_id: int) -> None:
        await self.ctx.emit(await self.reporting.daily_text(company_id))

    async def _weekly_report(self, company_id: int) -> None:
        await self.ctx.emit(await self.reporting.weekly(company_id))


__all__ = ["Alerts", "DemoResult", "DemoTime", "Moderation", "Ops", "OpsContext", "Optimization",
           "OptimizationResult", "Reporting", "StatsSync", "Thresholds"]
