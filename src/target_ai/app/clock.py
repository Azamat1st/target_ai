"""Часы приложения: симулированное время только на демо-уровне, иначе настоящее."""

from __future__ import annotations

from datetime import datetime, timedelta

from target_ai.clock import Clock, SimClock
from target_ai.config.runtime import DEFAULT_LEVEL, LEVEL_DEMO, SETTING_LEVEL


class LevelClock:
    """Перемотка демо не должна утекать в тест и боевой уровни: там время всегда настоящее."""

    def __init__(self, sim: SimClock, real: Clock, db) -> None:
        self._sim, self._real, self._db = sim, real, db

    def now(self) -> datetime:
        if self._db.settings.get(SETTING_LEVEL, DEFAULT_LEVEL) == LEVEL_DEMO:
            return self._sim.now()
        return self._real.now()

    def advance(self, delta: timedelta) -> None:
        self._sim.advance(delta)
