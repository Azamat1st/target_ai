"""Общее для сервисов управления: уровни, ответ сервиса, запись в журнал, отложенные намерения."""

from __future__ import annotations

import re
import asyncio
from dataclasses import dataclass, field
from typing import Any

from target_ai.clock import Clock
from target_ai.notify import Buttons
from target_ai.storage import AuditEntry, Database
from target_ai.config.runtime import SETTING_LEVEL

LEVELS = ("demo", "test", "live")
LEVEL_TITLES = {"demo": "Демо", "test": "Тест", "live": "Боевой"}

# Статусы ответа сервиса (Reply.status)
DONE = "done"
NEEDS_APPROVAL = "needs_approval"
DENIED = "denied"
ERROR = "error"
UNCHANGED = "unchanged"

ACTOR_USER = "user"


@dataclass(frozen=True)
class Reply:
    """Ответ сервиса: текст для пользователя и кнопки (строки пар «подпись, callback_data»)."""

    status: str
    text: str
    buttons: Buttons = field(default_factory=list)
    approval_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == DONE


def confirm_buttons(prefix: str, approval_id: str, yes: str = "✅ Подтвердить") -> Buttons:
    return [[(yes, f"{prefix}:ok:{approval_id}"), ("Отмена", f"{prefix}:no:{approval_id}")]]


async def io(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def level_title(level: str) -> str:
    return LEVEL_TITLES.get(level, level)


class Journal:
    """Запись в журнал действий (`audit`) от имени сервисов управления."""

    def __init__(self, db: Database, clock: Clock) -> None:
        self._db, self._clock = db, clock

    async def write(self, actor: str, action: str, company_id: int | None, outcome: str = "ok",
                    **details: Any) -> None:
        entry = AuditEntry(actor=actor, action=action, company_id=company_id,
                           data={**details, "outcome": outcome}, created_at=self._clock.now())
        await io(self._db.audit.create, entry)


def intent_of(db: Database, approval_id: str) -> dict[str, Any] | None:
    """Намерение «что сделать по кнопке» из `Approval.payload["intent"]`; None — подтверждения нет или оно не активно."""
    approval = db.approvals.get(approval_id)
    if approval is None or approval.status != "pending":
        return None
    return approval.payload.get("intent")


_ID_PREFIX = re.compile(r"(?m)^\d{9,}:\s*защитный лимит")
_RAW_ID = re.compile(r"\b\d{9,}\b:?\s*")


def hide_meta_ids(text: str) -> str:
    """Сырые ID объектов Meta пользователю не нужны: «123…: защитный лимит X» -> «Защитный лимит кампании: X»."""
    return _RAW_ID.sub("", _ID_PREFIX.sub("Защитный лимит кампании:", text))
