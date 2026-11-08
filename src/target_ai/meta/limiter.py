"""Ограничитель запросов на рекламный аккаунт: корзина баллов + паузы по заголовкам использования."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta

from target_ai.clock import Clock

Sleeper = Callable[[float], Awaitable[None]]

WINDOW_SECONDS = 300
WINDOW_POINTS = 60
READ_COST = 1
WRITE_COST = 3
USAGE_PAUSE_PCT = 90
DEFAULT_BLOCK_SECONDS = 300
INSIGHTS_THROTTLE_SECONDS = 60
GLOBAL_KEY = "*"


def usage_pause_seconds(headers: Mapping[str, str]) -> float | None:
    """Сколько секунд молчать по заголовкам использования (None — паузы не нужно)."""
    pauses = [
        _business_usage_pause(headers.get("x-business-use-case-usage")),
        _account_usage_pause(headers.get("x-ad-account-usage")),
        _insights_throttle_pause(headers.get("x-fb-ads-insights-throttle")),
    ]
    values = [p for p in pauses if p is not None]
    return max(values) if values else None


def _load(raw: str | None) -> object:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _business_usage_pause(raw: str | None) -> float | None:
    data = _load(raw)
    if not isinstance(data, dict):
        return None
    pause: float | None = None
    for entries in data.values():
        for entry in entries if isinstance(entries, list) else []:
            peak = max((entry.get(k, 0) for k in ("call_count", "total_cputime", "total_time")), default=0)
            minutes = entry.get("estimated_time_to_regain_access", 0) or 0
            if minutes > 0:
                pause = max(pause or 0.0, minutes * 60.0)
            elif peak >= USAGE_PAUSE_PCT:
                pause = max(pause or 0.0, float(DEFAULT_BLOCK_SECONDS))
    return pause


def _account_usage_pause(raw: str | None) -> float | None:
    data = _load(raw)
    if not isinstance(data, dict):
        return None
    reset = data.get("reset_time_duration") or 0
    if reset > 0:
        return float(reset)
    if data.get("acc_id_util_pct", 0) >= USAGE_PAUSE_PCT:
        return float(DEFAULT_BLOCK_SECONDS)
    return None


def _insights_throttle_pause(raw: str | None) -> float | None:
    data = _load(raw)
    if isinstance(data, dict) and max(data.get("acc_id_util_pct", 0), data.get("app_id_util_pct", 0)) >= USAGE_PAUSE_PCT:
        return float(INSIGHTS_THROTTLE_SECONDS)
    return None


class RequestLimiter:
    """60 баллов за 300 с на рекламный аккаунт (чтение 1, запись 3) и блокировки до сброса."""

    def __init__(self, clock: Clock, sleeper: Sleeper) -> None:
        self._clock = clock
        self._sleeper = sleeper
        self._spent: dict[str, deque[tuple[datetime, int]]] = defaultdict(deque)
        self._blocked_until: dict[str, datetime] = {}

    def block(self, key: str | None, seconds: float) -> None:
        until = self._clock.now() + timedelta(seconds=seconds)
        slot = key or GLOBAL_KEY
        current = self._blocked_until.get(slot)
        if current is None or until > current:
            self._blocked_until[slot] = until

    async def acquire(self, key: str | None, cost: int) -> None:
        """Ждёт, пока можно слать запрос, и списывает баллы. key=None — запрос вне аккаунта."""
        wait = self._blocked_wait(key)
        if wait > 0:
            await self._sleeper(wait)
        if key is None:
            return
        wait = self._budget_wait(key, cost)
        if wait > 0:
            await self._sleeper(wait)
        self._spent[key].append((self._clock.now(), cost))

    def _blocked_wait(self, key: str | None) -> float:
        now = self._clock.now()
        untils = [self._blocked_until.get(GLOBAL_KEY)]
        if key is not None:
            untils.append(self._blocked_until.get(key))
        waits = [(u - now).total_seconds() for u in untils if u is not None]
        return max(waits, default=0.0)

    def _budget_wait(self, key: str, cost: int) -> float:
        now = self._clock.now()
        window = self._spent[key]
        while window and (now - window[0][0]).total_seconds() >= WINDOW_SECONDS:
            window.popleft()
        total = sum(points for _, points in window)
        for stamp, points in window:
            if total + cost <= WINDOW_POINTS:
                return 0.0
            total -= points
            wait_until = stamp + timedelta(seconds=WINDOW_SECONDS)
            if total + cost <= WINDOW_POINTS:
                return (wait_until - now).total_seconds()
        return 0.0
