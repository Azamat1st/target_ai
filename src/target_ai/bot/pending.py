"""Ожидание ответа пользователя (значение настройки, сумма лимита): хранится в `dialog_state`."""

from __future__ import annotations

import asyncio

from target_ai.storage import DialogState

PREFIX = "bot:"
SETTING = "bot:set:"
LIMIT = "bot:limit:"


async def set_pending(app, chat_id: int, state: str) -> None:
    dialog = DialogState(chat_id=chat_id, state=state, data={}, updated_at=app.clock.now())
    await asyncio.to_thread(app.db.dialogs.save, dialog)


async def get_pending(app, chat_id: int) -> str | None:
    dialog = await asyncio.to_thread(app.db.dialogs.get, chat_id)
    return dialog.state if dialog is not None and dialog.state.startswith(PREFIX) else None


async def clear_pending(app, chat_id: int) -> None:
    """Снимает только свои ожидания; анкету и правку плана (сервисы «до запуска») не трогает."""
    if await get_pending(app, chat_id) is not None:
        await asyncio.to_thread(app.db.dialogs.delete, chat_id)
