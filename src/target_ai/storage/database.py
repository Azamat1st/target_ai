"""Database: соединение SQLite, миграции, транзакции.

Синхронный API; из asyncio вызывается через ``asyncio.to_thread``. Одно общее
соединение под RLock: приложение однопользовательское, нагрузка мала.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from .errors import StorageError

_MIGRATION_NAME = re.compile(r"^(\d{4})_[\w]+\.sql$")


class Database:
    def __init__(self, path: str | Path) -> None:
        self._lock = threading.RLock()
        self._depth = 0
        if str(path) != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()
        self._attach_repositories()

    # --- низкий уровень (используют репозитории) ---
    def _execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def _query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Атомарный блок: исключение внутри откатывает всё. Вложенные — savepoint."""
        with self._lock:
            outer = self._depth == 0
            name = f"sp_{self._depth}"
            self._conn.execute("BEGIN IMMEDIATE" if outer else f"SAVEPOINT {name}")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                self._conn.execute("ROLLBACK" if outer else f"ROLLBACK TO {name}")
                if not outer:
                    self._conn.execute(f"RELEASE {name}")
                raise
            self._depth -= 1
            self._conn.execute("COMMIT" if outer else f"RELEASE {name}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- миграции ---
    def _migrate(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        applied = {r["version"] for r in self._conn.execute("SELECT version FROM schema_migrations")}
        for version, name, sql in _load_migrations():
            if version in applied:
                continue
            with self.transaction():
                for statement in _split_statements(sql):
                    self._conn.execute(statement)
                self._conn.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, name))

    def _attach_repositories(self) -> None:
        from .repositories import build_repositories

        for name, repo in build_repositories(self).items():
            setattr(self, name, repo)


def _load_migrations() -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    for item in resources.files("target_ai.storage").joinpath("migrations").iterdir():
        match = _MIGRATION_NAME.match(item.name)
        if match:
            found.append((int(match.group(1)), item.name, item.read_text(encoding="utf-8")))
    if not found:
        raise StorageError("Не найдены файлы миграций базы.")
    return sorted(found)


def _split_statements(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";\n") if s.strip()]
