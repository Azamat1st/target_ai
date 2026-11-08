"""Первым в цепочке — доступ по белому списку; затем защита от повторных апдейтов."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Update

from target_ai.storage import AuditEntry

log = logging.getLogger(__name__)

DENIED_TEXT = "Доступ закрыт."
KEEP_UPDATES = 1000


class AccessMiddleware(BaseMiddleware):
    """Чужой ID: «Доступ закрыт.» и больше ничего; в журнал — не чаще раза в час на ID."""

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, handler, event: Update, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return None
        if user.id in self._app.settings_service.allowed_ids():
            return await handler(event, data)
        await self._deny(event, user.id)
        return None

    async def _deny(self, event: Update, user_id: int) -> None:
        bot = self._app.bot
        try:
            if event.callback_query is not None:
                await bot.answer_callback_query(event.callback_query.id, text=DENIED_TEXT, show_alert=True)
            elif event.message is not None:
                await bot.send_message(event.message.chat.id, DENIED_TEXT)
        except TelegramAPIError:
            log.warning("Не удалось ответить чужому ID %s", user_id)
        await asyncio.to_thread(self._journal, user_id)

    def _journal(self, user_id: int) -> None:
        app, now = self._app, self._app.clock.now()
        if not app.db.alerts.mark(f"denied:{user_id}:{now:%Y%m%d%H}", now):
            return
        app.db.audit.create(AuditEntry(actor="system", action="access_denied", company_id=None, created_at=now,
                                       data={"outcome": "denied", "summary": f"Telegram ID {user_id}",
                                             "telegram_id": user_id}))



class DedupeMiddleware(BaseMiddleware):
    """Повтор `update_id` (Telegram, сбой сети) игнорируется; хранятся последние 1 000."""

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, handler, event: Update, data: dict[str, Any]) -> Any:
        first = await asyncio.to_thread(self._mark, event.update_id)
        if not first:
            log.info("Повтор апдейта %s проигнорирован", event.update_id)
            return None
        return await handler(event, data)

    def _mark(self, update_id: int) -> bool:
        db = self._app.db
        first = db.updates.mark(update_id, self._app.clock.now())
        if first:
            db.updates.prune(KEEP_UPDATES)
        return first
