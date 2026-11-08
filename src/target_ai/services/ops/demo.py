"""Перемотка демо (истории 85, 86): симулятор → статистика → задачи, один итог на N дней."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from .common import OpsContext
from .reporting import Reporting

MAX_DAYS = 31
DAILY_PREFIX, WEEKLY_PREFIX, OPTIMIZER_PREFIX = "Сводка —", "Недельный разбор —", "Оптимизация —"
DAY = timedelta(days=1)


@dataclass(frozen=True)
class DemoResult:
    text: str
    days: int
    day_lines: tuple[str, ...] = ()
    changes: tuple[str, ...] = ()
    buttons: list = field(default_factory=list)


class DemoTime:
    """`simulate_day(date)` — `Simulator.simulate_day`; `scheduler` — с теми же часами (`SimClock`)."""

    def __init__(self, ctx: OpsContext, scheduler, simulator, reporting: Reporting) -> None:
        self._ctx, self._scheduler, self._sim, self._reporting = ctx, scheduler, simulator, reporting

    async def advance(self, days: int) -> DemoResult:
        ctx = self._ctx
        if not 1 <= days <= MAX_DAYS:
            raise ValueError(f"Перемотка — от 1 до {MAX_DAYS} дней.")
        if ctx.db.settings.get("level", "demo") != "demo":
            raise ValueError("Перемотка времени доступна только на демо-уровне.")
        day_lines, extra, weekly, buttons = [], [], None, []
        with ctx.quiet() as outbox:
            for _ in range(days):
                self._sim.simulate_day(ctx.clock.now().date())
                mark = len(outbox)
                await self._scheduler.run_until(ctx.clock.now() + DAY)
                day_lines += await self._day_lines()
                for text, btn in outbox[mark:]:
                    if text.startswith(WEEKLY_PREFIX):
                        weekly = text
                    elif not text.startswith(DAILY_PREFIX):
                        extra.append(text)
                        buttons += btn
        text = self._compose(days, day_lines, extra, weekly)
        await ctx.emit(text, buttons)
        return DemoResult(text, days, tuple(day_lines), tuple(extra), buttons)

    async def _day_lines(self) -> list[str]:
        return [await self._reporting.day_line(c.id) for c in await self._ctx.active_companies()]

    @staticmethod
    def _compose(days: int, day_lines: list[str], extra: list[str], weekly: str | None) -> str:
        parts = [f"⏩ Перемотка на {days} дн.", "\n".join(day_lines)]
        if extra:
            parts.append("Изменения и события:\n" + "\n\n".join(extra))
        if weekly:
            parts.append(weekly)
        return "\n\n".join(p for p in parts if p)
