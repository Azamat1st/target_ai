"""Расписание задач (спека §11): интервалы и суточные моменты в часовом поясе."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

STATS, MODERATION, ALERTS, TOKEN = "stats_sync", "moderation", "alerts", "token_check"
OPTIMIZE, DAILY_REPORT, WEEKLY_REPORT = "daily_optimization", "daily_report", "weekly_report"

# порядок исполнения в одном тике: сначала данные, потом решения и отчёты
KIND_ORDER = (STATS, MODERATION, ALERTS, TOKEN, OPTIMIZE, DAILY_REPORT, WEEKLY_REPORT)
COMPANY_KINDS = (STATS, MODERATION, ALERTS, OPTIMIZE, DAILY_REPORT, WEEKLY_REPORT)
GLOBAL_KINDS = (TOKEN,)
INTERVALS = {STATS: timedelta(hours=3), MODERATION: timedelta(minutes=15),
             ALERTS: timedelta(minutes=15), TOKEN: timedelta(hours=6)}
OPTIMIZE_AT = time(7, 0)
MONDAY = 0


def parse_hhmm(raw: str) -> time:
    hours, minutes = raw.split(":")
    return time(int(hours), int(minutes))


def next_due(kind: str, now: datetime, tz_name: str, report_at: time) -> datetime:
    """Ближайший момент строго после `now` (aware, UTC)."""
    if kind in INTERVALS:
        return now + INTERVALS[kind]
    zone = ZoneInfo(tz_name)
    local = now.astimezone(zone)
    at = OPTIMIZE_AT if kind == OPTIMIZE else report_at
    candidate = local.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    while candidate <= local or (kind == WEEKLY_REPORT and candidate.weekday() != MONDAY):
        candidate = (candidate + timedelta(days=1)).replace(hour=at.hour, minute=at.minute)
    return candidate.astimezone(now.tzinfo)
