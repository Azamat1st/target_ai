"""Универсальный репозиторий для таблиц с целочисленным id."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from .errors import NotFound
from .mapping import column_names, from_row, to_row

if TYPE_CHECKING:
    from .database import Database

T = TypeVar("T")


class Repo(Generic[T]):
    table: str
    model: type[T]

    def __init__(self, db: Database) -> None:
        self._db = db
        self._columns = set(column_names(self.model))

    def _insert_row(self, obj: T) -> dict[str, Any]:
        row = to_row(obj)
        if row.get("id") == 0:
            row.pop("id")
        return row

    def create(self, obj: T) -> T:
        row = self._insert_row(obj)
        cols = ", ".join(row)
        marks = ", ".join(f":{c}" for c in row)
        cur = self._db._execute(f"INSERT INTO {self.table} ({cols}) VALUES ({marks})", row)
        return replace(obj, id=cur.lastrowid) if "id" not in row else obj  # type: ignore[type-var]

    def get(self, item_id: int) -> T | None:
        row = self._db._query_one(f"SELECT * FROM {self.table} WHERE id = ?", (item_id,))
        return None if row is None else from_row(self.model, row)

    def list(self, *, order_by: str = "id", limit: int | None = None, **filters: Any) -> list[T]:
        where, params = self._where(filters)
        sql = f"SELECT * FROM {self.table}{where} ORDER BY {self._order(order_by)}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [from_row(self.model, r) for r in self._db._query(sql, params)]

    def update(self, obj: T) -> T:
        row = to_row(obj)
        item_id = row.pop("id")
        sets = ", ".join(f"{c} = :{c}" for c in row)
        cur = self._db._execute(f"UPDATE {self.table} SET {sets} WHERE id = :_id", {**row, "_id": item_id})
        if cur.rowcount == 0:
            raise NotFound(f"{self.table}: запись {item_id} не найдена.")
        return obj

    def _where(self, filters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        clauses, params = [], {}
        for i, (col, value) in enumerate(filters.items()):
            if col not in self._columns:
                raise ValueError(f"{self.table}: нет колонки «{col}».")
            if value is None:
                clauses.append(f"{col} IS NULL")
            else:
                clauses.append(f"{col} = :f{i}")
                params[f"f{i}"] = value
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def _order(self, order_by: str) -> str:
        column = order_by.removeprefix("-")
        if column not in self._columns:
            raise ValueError(f"{self.table}: нельзя сортировать по «{order_by}».")
        return f"{column} DESC" if order_by.startswith("-") else column
