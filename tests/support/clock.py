"""Неподвижное «настоящее» время для тестов, которые собирают приложение целиком.

Без него ожидаемые суммы зависят от дня запуска: дневной бюджет — остаток месяца на число оставшихся дней.
"""

from __future__ import annotations

from datetime import UTC, datetime

# 8 ноября 2026, 18:00 UTC: в Берлине и Нью-Йорке ещё 8-е (23 дня до конца месяца), в Токио уже 9-е (22 дня).
FIXED_NOW = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)


class FixedClock:
    """Часы, которые всегда показывают одно и то же время; перемотку демо добавляет SimClock поверх."""

    def __init__(self, moment: datetime = FIXED_NOW) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment
