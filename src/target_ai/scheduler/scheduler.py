"""Планировщик: задачи в таблице `jobs`, время — только из Clock; переживает перезапуск."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from target_ai.logging_setup import mask_secrets
from target_ai.storage import Database, Job

from . import schedule as sch

log = logging.getLogger(__name__)

Handler = Callable[[int], Awaitable[object]]
DEFAULT_REPORT_TIME = "10:00"
DEFAULT_TZ = "UTC"


@dataclass(frozen=True)
class JobRun:
    kind: str
    company_id: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Scheduler:
    """`handlers`: вид задачи → async-функция(company_id). Ошибка одной задачи не останавливает остальные."""

    def __init__(self, db: Database, clock, handlers: Mapping[str, Handler], notifier=None) -> None:
        self._db, self._clock, self._handlers, self._notifier = db, clock, dict(handlers), notifier

    async def tick(self) -> list[JobRun]:
        """Выполняет просроченные задачи по одному разу и переносит каждую на следующий срок."""
        self.ensure_jobs()
        now = self._clock.now()
        due = sorted(self._db.jobs.due(now), key=lambda j: (self._rank(j.kind), j.due_at, j.company_id))
        runs = [await self._run(job) for job in due]
        return [r for r in runs if r is not None]

    async def run_until(self, moment: datetime) -> list[JobRun]:
        """Перемотка: симулированные часы ведутся до `moment`, затем выполняется всё просроченное."""
        advance = getattr(self._clock, "advance", None)
        if advance is not None and moment > self._clock.now():
            advance(moment - self._clock.now())
        return await self.tick()

    def rebase(self, now: datetime) -> int:
        """Часы сменились (демо ↔ настоящие): задачи с интервалом идут на «сейчас», суточные - на ближайший срок
        по новым часам. Вызывать только при смене часов, не при любой смене уровня."""
        jobs = self._db.jobs.list()
        for job in jobs:
            due = now if job.kind in sch.INTERVALS else self._next(job.kind, job.company_id)
            self._db.jobs.update(replace(job, due_at=due))
        return len(jobs)

    def ensure_jobs(self) -> None:
        """Создаёт недостающие задачи: по каждой активной компании и глобальные."""
        now, jobs = self._clock.now(), self._db.jobs
        for kind in sch.GLOBAL_KINDS:
            if not jobs.list(kind=kind, company_id=0):
                jobs.schedule(kind, now, 0)
        for company in self._db.companies.list():
            if company.status != "active" or not company.ad_account_id:
                continue
            for kind in sch.COMPANY_KINDS:
                if not jobs.list(kind=kind, company_id=company.id):
                    jobs.schedule(kind, now if kind in sch.INTERVALS else self._next(kind, company.id), company.id)

    # ------------------------------------------------------------------ внутреннее
    @staticmethod
    def _rank(kind: str) -> int:
        return sch.KIND_ORDER.index(kind) if kind in sch.KIND_ORDER else len(sch.KIND_ORDER)

    def _next(self, kind: str, company_id: int) -> datetime:
        company = self._db.companies.get(company_id) if company_id else None
        tz = self._db.settings.get("report_tz") or (company.timezone if company else None) or DEFAULT_TZ
        at = sch.parse_hhmm(self._db.settings.get("report_time") or DEFAULT_REPORT_TIME)
        return sch.next_due(kind, self._clock.now(), tz, at)

    async def _run(self, job: Job) -> JobRun | None:
        handler = self._handlers.get(job.kind)
        company = self._db.companies.get(job.company_id) if job.company_id else None
        skip = handler is None or (job.company_id and (company is None or company.status != "active"))
        error = None
        if not skip:
            try:
                await handler(job.company_id)
            except Exception as exc:  # задача упала: остальные продолжают, ошибка уходит уведомлением
                log.exception("Задача %s (компания %s) завершилась ошибкой", job.kind, job.company_id)
                error = getattr(exc, "user_message_ru", None) or str(exc) or type(exc).__name__
                await self._report(job, mask_secrets(error))
        self._db.jobs.update(replace(job, due_at=self._next(job.kind, job.company_id), last_run_at=self._clock.now()))
        return None if skip else JobRun(job.kind, job.company_id, error)

    async def _report(self, job: Job, error: str) -> None:
        if self._notifier is None:
            return
        where = f" (компания {job.company_id})" if job.company_id else ""
        try:
            await self._notifier.send(None, f"Задача «{job.kind}»{where} не выполнена: {error}", [])
        except Exception:
            log.exception("Не удалось сообщить об ошибке задачи")
