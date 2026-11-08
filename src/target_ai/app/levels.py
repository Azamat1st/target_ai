"""Смена уровня с учётом часов: демо живёт на симулированном времени, остальные уровни - на настоящем."""

from __future__ import annotations

import asyncio

from target_ai.services.admin import LevelSwitch


class ClockAwareLevelSwitch(LevelSwitch):
    """После смены уровня, при которой сменились часы, переносит задачи планировщика на новые часы."""

    def __init__(self, *args, scheduler) -> None:
        super().__init__(*args)
        self._scheduler = scheduler

    async def request(self, target: str, **kwargs):
        before = await self.current()
        return await self._rebased(before, await super().request(target, **kwargs))

    async def confirm(self, approval_id: str):
        before = await self.current()
        return await self._rebased(before, await super().confirm(approval_id))

    async def _rebased(self, before: str, reply):
        after = await self.current()
        if (before == "demo") != (after == "demo"):
            await asyncio.to_thread(self._scheduler.rebase, self._clock.now())
        return reply
