"""Планировщик в процессе приложения: фоновый тик и общий замок с перемоткой демо."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

log = logging.getLogger(__name__)

DEFAULT_TICK_SECONDS = 30.0


class LockedScheduler:
    """Тик по таймеру и перемотка демо не должны выполнять одни и те же задачи одновременно."""

    def __init__(self, scheduler) -> None:
        self._scheduler = scheduler
        self._lock = asyncio.Lock()

    async def tick(self):
        async with self._lock:
            return await self._scheduler.tick()

    def rebase(self, now: datetime) -> int:
        return self._scheduler.rebase(now)

    async def run_until(self, moment: datetime):
        async with self._lock:
            return await self._scheduler.run_until(moment)


class Ticker:
    def __init__(self, scheduler: LockedScheduler, interval: float = DEFAULT_TICK_SECONDS) -> None:
        self._scheduler, self._interval = scheduler, interval
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="scheduler-ticker")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        while True:
            try:
                await self._scheduler.tick()
            except Exception:  # сбой тика не должен убивать расписание
                log.exception("Тик планировщика завершился ошибкой")
            await asyncio.sleep(self._interval)
