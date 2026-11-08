"""Общие мелочи сервисов «до запуска»: разбиение сообщений, колбэки подтверждений, выдача кнопок.

Формат колбэка (спека §8): `ap:<approval_id>:<choice>` (≤ 64 байт), всё остальное — в `approvals.payload`.
Тексты — обычные (без HTML-разметки): экранирует реализация `Notifier`.
"""

from __future__ import annotations

from datetime import timedelta

from target_ai.storage import Approval, Database

TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 4000
CALLBACK_PREFIX = "ap"
CALLBACK_MAX_BYTES = 64
NOT_FOUND_RU = "не найдено"


def callback_data(approval_id: str, choice: str) -> str:
    data = f"{CALLBACK_PREFIX}:{approval_id}:{choice}"
    if len(data.encode()) > CALLBACK_MAX_BYTES:
        raise ValueError("callback_data длиннее 64 байт")
    return data


def parse_callback(data: str) -> tuple[str, str] | None:
    """`ap:<id>:<choice>` -> (id, choice); чужой формат -> None."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def split_message(blocks: list[str], limit: int = SAFE_LIMIT) -> list[str]:
    """Склеивает блоки (разделы) в сообщения ≤ limit; слишком длинный блок режется по строкам."""
    pieces: list[str] = []
    for block in blocks:
        pieces.extend(_split_block(block, limit))
    messages: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n\n{piece}" if current else piece
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            messages.append(current)
        current = piece
    if current:
        messages.append(current)
    return messages


def _split_block(block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]
    parts, current = [], ""
    for line in block.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            candidate = line
        current = candidate
    if current:
        parts.append(current)
    return parts


def issue(db: Database, now, kind: str, company_id: int | None, payload: dict,
          ttl: timedelta | None = None) -> Approval:
    """Создаёт одноразовое подтверждение под кнопку (хранится в базе)."""
    kwargs = {"ttl": ttl} if ttl is not None else {}
    return db.approvals.create(kind=kind, now=now, company_id=company_id, payload=payload, **kwargs)


def button(db: Database, now, label: str, kind: str, choice: str, company_id: int | None,
           payload: dict) -> tuple[str, str]:
    approval = issue(db, now, kind, company_id, payload)
    return label, callback_data(approval.id, choice)
