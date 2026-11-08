"""Время: единственный источник «сейчас» (прямой datetime.now() вне модуля запрещён)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now(self) -> datetime:
        """Aware datetime в UTC."""
        ...


class OffsetStore(Protocol):
    """Хранилище смещения симулированного времени (в базе — репозиторий sim)."""

    def get_offset(self) -> timedelta: ...

    def set_offset(self, value: timedelta) -> None: ...


class RealClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SimClock:
    """Реальное время плюс сохранённое смещение; перемотка переживает перезапуск."""

    def __init__(self, store: OffsetStore, base: Clock | None = None) -> None:
        self._store = store
        self._base = base or RealClock()

    def now(self) -> datetime:
        return self._base.now() + self._store.get_offset()

    def advance(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise ValueError("Время можно только перематывать вперёд.")
        self._store.set_offset(self._store.get_offset() + delta)


def to_account_time(moment: datetime, tz_name: str) -> datetime:
    """Переводит aware-момент в часовой пояс рекламного аккаунта."""
    if moment.tzinfo is None:
        raise ValueError("Ожидается aware datetime.")
    return moment.astimezone(ZoneInfo(tz_name))


__all__ = ["Clock", "OffsetStore", "RealClock", "SimClock", "to_account_time"]
