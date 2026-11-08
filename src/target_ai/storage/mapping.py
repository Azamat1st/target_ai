"""Преобразование frozen-dataclass <-> строка SQLite.

Виды полей задаются метаданными: json, dt (aware UTC), money (две колонки
``<имя>_minor`` и ``<имя>_currency``), bool. Остальные — как есть.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import field, fields
from datetime import UTC, datetime
from typing import Any, TypeVar

from target_ai.money import Money

T = TypeVar("T")


def json_field(factory: Callable[[], Any] = dict):
    return field(default_factory=factory, metadata={"kind": "json"})


def dt_field():
    return field(default=None, metadata={"kind": "dt"})


def dt_required():
    return field(metadata={"kind": "dt"})


def money_field():
    return field(default=None, metadata={"kind": "money"})


def encode_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("Время в базе хранится только как aware datetime.")
    return value.astimezone(UTC).isoformat()


def decode_dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def column_names(model: type) -> list[str]:
    names: list[str] = []
    for f in fields(model):
        if f.metadata.get("kind") == "money":
            names += [f"{f.name}_minor", f"{f.name}_currency"]
        else:
            names.append(f.name)
    return names


def to_row(obj: Any) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for f in fields(obj):
        value = getattr(obj, f.name)
        kind = f.metadata.get("kind")
        if kind == "money":
            row[f"{f.name}_minor"] = None if value is None else value.amount_minor
            row[f"{f.name}_currency"] = None if value is None else value.currency
        elif kind == "json":
            row[f.name] = json.dumps(value, ensure_ascii=False)
        elif kind == "dt":
            row[f.name] = encode_dt(value)
        elif kind == "bool":
            row[f.name] = int(value)
        else:
            row[f.name] = value
    return row


def from_row(model: type[T], row: sqlite3.Row) -> T:
    values: dict[str, Any] = {}
    for f in fields(model):  # type: ignore[arg-type]
        kind = f.metadata.get("kind")
        if kind == "money":
            minor, cur = row[f"{f.name}_minor"], row[f"{f.name}_currency"]
            values[f.name] = None if minor is None else Money(minor, cur)
        elif kind == "json":
            values[f.name] = json.loads(row[f.name])
        elif kind == "dt":
            values[f.name] = decode_dt(row[f.name])
        elif kind == "bool":
            values[f.name] = bool(row[f.name])
        else:
            values[f.name] = row[f.name]
    return model(**values)  # type: ignore[call-arg]

