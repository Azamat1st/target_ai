"""Репозитории: обычные (id + get/list/create/update) и специальные."""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .errors import AlreadyLaunched
from .mapping import encode_dt, from_row, to_row
from .models import (
    Approval, AuditEntry, Company, DailyStat, DialogState, Fact, HistoryEntry, Job, Launch,
    LaunchStep, MediaAsset, MetaObject, Passport, Plan,
)
from .repo_base import Repo

if TYPE_CHECKING:
    from .database import Database

DEFAULT_APPROVAL_TTL = timedelta(hours=24)
_APPROVAL_FILTERS = frozenset({"id", "kind", "company_id", "status"})


class CompaniesRepo(Repo[Company]):
    table, model = "companies", Company


class FactsRepo(Repo[Fact]):
    table, model = "facts", Fact


class MediaRepo(Repo[MediaAsset]):
    table, model = "media_assets", MediaAsset


class LaunchStepsRepo(Repo[LaunchStep]):
    table, model = "launch_steps", LaunchStep


class HistoryRepo(Repo[HistoryEntry]):
    table, model = "history", HistoryEntry


class _Versioned:
    def latest(self, company_id: int):
        rows = self.list(company_id=company_id, order_by="-version", limit=1)  # type: ignore[attr-defined]
        return rows[0] if rows else None


class PassportsRepo(_Versioned, Repo[Passport]):
    table, model = "passports", Passport


class PlansRepo(_Versioned, Repo[Plan]):
    table, model = "plans", Plan


class LaunchesRepo(Repo[Launch]):
    table, model = "launches", Launch

    def create(self, obj: Launch) -> Launch:
        try:
            return super().create(obj)
        except sqlite3.IntegrityError as exc:
            if "launches.plan_id" in str(exc):
                raise AlreadyLaunched(obj.plan_id) from exc
            raise

    def get_by_plan(self, plan_id: int) -> Launch | None:
        rows = self.list(plan_id=plan_id, limit=1)
        return rows[0] if rows else None


class ObjectsRepo(Repo[MetaObject]):
    table, model = "meta_objects", MetaObject

    def get_by_meta_id(self, meta_id: str) -> MetaObject | None:
        rows = self.list(meta_id=meta_id, limit=1)
        return rows[0] if rows else None


class AuditRepo(Repo[AuditEntry]):
    """Журнал только дописывается: изменять записи нельзя."""

    table, model = "audit_log", AuditEntry

    def update(self, obj: AuditEntry) -> AuditEntry:
        raise NotImplementedError("Журнал аудита не изменяется.")


class StatsRepo(Repo[DailyStat]):
    table, model = "stats_daily", DailyStat

    def upsert(self, stat: DailyStat) -> DailyStat:
        """Одна строка на (объект, дата аккаунта): повторная загрузка заменяет."""
        row = {k: v for k, v in to_row(stat).items() if k != "id"}
        cols, marks = ", ".join(row), ", ".join(f":{c}" for c in row)
        updates = ", ".join(f"{c} = excluded.{c}" for c in row if c not in ("meta_id", "date"))
        self._db._execute(
            f"INSERT INTO stats_daily ({cols}) VALUES ({marks}) "
            f"ON CONFLICT(meta_id, date) DO UPDATE SET {updates}",
            row,
        )
        stored = self.list(meta_id=stat.meta_id, date=stat.date, limit=1)
        return stored[0]

    def list_range(self, company_id: int, start: str, end: str) -> list[DailyStat]:
        """Даты аккаунта в формате ISO, границы включительно."""
        rows = self._db._query(
            "SELECT * FROM stats_daily WHERE company_id = ? AND date BETWEEN ? AND ? ORDER BY date, meta_id",
            (company_id, start, end),
        )
        return [from_row(DailyStat, r) for r in rows]


class JobsRepo(Repo[Job]):
    table, model = "jobs", Job

    def schedule(self, kind: str, due_at: datetime, company_id: int = 0) -> Job:
        """Создаёт или переносит задачу (kind, company_id)."""
        self._db._execute(
            "INSERT INTO jobs (kind, company_id, due_at) VALUES (?, ?, ?) "
            "ON CONFLICT(kind, company_id) DO UPDATE SET due_at = excluded.due_at",
            (kind, company_id, encode_dt(due_at)),
        )
        return self.list(kind=kind, company_id=company_id, limit=1)[0]

    def due(self, now: datetime) -> list[Job]:
        rows = self._db._query("SELECT * FROM jobs WHERE due_at <= ? ORDER BY due_at, id", (encode_dt(now),))
        return [from_row(Job, r) for r in rows]


class ApprovalsRepo:
    """Одноразовые подтверждения; id — случайный токен, не счётчик."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, *, kind: str, now: datetime, company_id: int | None = None,
               payload: dict[str, Any] | None = None, ttl: timedelta = DEFAULT_APPROVAL_TTL) -> Approval:
        approval = Approval(
            id=secrets.token_hex(8), kind=kind, company_id=company_id, payload=payload or {},
            created_at=now, expires_at=now + ttl,
        )
        row = to_row(approval)
        cols, marks = ", ".join(row), ", ".join(f":{c}" for c in row)
        self._db._execute(f"INSERT INTO approvals ({cols}) VALUES ({marks})", row)
        return approval

    def get(self, approval_id: str) -> Approval | None:
        row = self._db._query_one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return None if row is None else from_row(Approval, row)

    def list(self, **filters: Any) -> list[Approval]:
        unknown = set(filters) - _APPROVAL_FILTERS
        if unknown:
            raise ValueError(f"approvals: нет колонок для фильтра {sorted(unknown)}.")
        clauses = [f"{col} IS NULL" if v is None else f"{col} = :{col}" for col, v in filters.items()]
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params = {k: v for k, v in filters.items() if v is not None}
        rows = self._db._query(f"SELECT * FROM approvals{where} ORDER BY created_at", params)
        return [from_row(Approval, r) for r in rows]

    def consume(self, approval_id: str, now: datetime) -> Approval | None:
        """Атомарно потребляет подтверждение. None — не потреблено (нет, использовано, просрочено)."""
        cur = self._db._execute(
            "UPDATE approvals SET status = 'consumed', consumed_at = ? "
            "WHERE id = ? AND status = 'pending' AND expires_at > ?",
            (encode_dt(now), approval_id, encode_dt(now)),
        )
        return self.get(approval_id) if cur.rowcount == 1 else None

    def cancel(self, approval_id: str) -> bool:
        cur = self._db._execute(
            "UPDATE approvals SET status = 'cancelled' WHERE id = ? AND status = 'pending'", (approval_id,)
        )
        return cur.rowcount == 1


class KeyValueRepo:
    """JSON-значения по ключу: settings и fake_meta_store."""

    def __init__(self, db: Database, table: str) -> None:
        self._db, self._table = db, table

    def get(self, key: str, default: Any = None) -> Any:
        row = self._db._query_one(f"SELECT value FROM {self._table} WHERE key = ?", (key,))
        return default if row is None else json.loads(row["value"])

    def set(self, key: str, value: Any) -> None:
        self._db._execute(
            f"INSERT INTO {self._table} (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def list(self) -> dict[str, Any]:
        return {r["key"]: json.loads(r["value"]) for r in self._db._query(f"SELECT * FROM {self._table}")}

    def delete(self, key: str) -> None:
        self._db._execute(f"DELETE FROM {self._table} WHERE key = ?", (key,))


class _MarkRepo:
    """Дедупликация: mark возвращает True только при первом вызове с этим ключом."""

    table: str
    key_column: str
    time_column: str

    def __init__(self, db: Database) -> None:
        self._db = db

    def mark(self, key: Any, now: datetime) -> bool:
        cur = self._db._execute(
            f"INSERT OR IGNORE INTO {self.table} ({self.key_column}, {self.time_column}) VALUES (?, ?)",
            (key, encode_dt(now)),
        )
        return cur.rowcount == 1

    def seen(self, key: Any) -> bool:
        return self._db._query_one(f"SELECT 1 FROM {self.table} WHERE {self.key_column} = ?", (key,)) is not None


class AlertsRepo(_MarkRepo):
    table, key_column, time_column = "alerts_sent", "key", "sent_at"


class UpdatesRepo(_MarkRepo):
    table, key_column, time_column = "processed_updates", "update_id", "processed_at"


class DialogsRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, chat_id: int) -> DialogState | None:
        row = self._db._query_one("SELECT * FROM dialog_state WHERE chat_id = ?", (chat_id,))
        return None if row is None else from_row(DialogState, row)

    def save(self, state: DialogState) -> DialogState:
        row = to_row(state)
        cols, marks = ", ".join(row), ", ".join(f":{c}" for c in row)
        updates = ", ".join(f"{c} = excluded.{c}" for c in row if c != "chat_id")
        self._db._execute(
            f"INSERT INTO dialog_state ({cols}) VALUES ({marks}) ON CONFLICT(chat_id) DO UPDATE SET {updates}",
            row,
        )
        return state

    def delete(self, chat_id: int) -> None:
        self._db._execute("DELETE FROM dialog_state WHERE chat_id = ?", (chat_id,))


class SimRepo:
    """Смещение симулированного времени; реализует clock.OffsetStore."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_offset(self) -> timedelta:
        row = self._db._query_one("SELECT offset_us FROM sim_state WHERE id = 1")
        return timedelta(microseconds=row["offset_us"])

    def set_offset(self, value: timedelta) -> None:
        self._db._execute("UPDATE sim_state SET offset_us = ? WHERE id = 1", (value // timedelta(microseconds=1),))


def build_repositories(db: Database) -> dict[str, Any]:
    return {
        "companies": CompaniesRepo(db), "facts": FactsRepo(db), "passports": PassportsRepo(db),
        "media": MediaRepo(db), "plans": PlansRepo(db), "launches": LaunchesRepo(db),
        "launch_steps": LaunchStepsRepo(db), "objects": ObjectsRepo(db), "approvals": ApprovalsRepo(db),
        "stats": StatsRepo(db), "history": HistoryRepo(db), "audit": AuditRepo(db), "jobs": JobsRepo(db),
        "settings": KeyValueRepo(db, "settings"), "fake_meta": KeyValueRepo(db, "fake_meta_store"),
        "alerts": AlertsRepo(db), "updates": UpdatesRepo(db), "dialogs": DialogsRepo(db), "sim": SimRepo(db),
    }

