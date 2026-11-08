"""Telegram-бот: тонкий слой между aiogram и сервисами."""

from __future__ import annotations

import logging

from aiogram import Dispatcher
from aiogram.types import ErrorEvent

from target_ai.logging_setup import mask_secrets

from . import callbacks, commands, media, text
from .middleware import AccessMiddleware, DedupeMiddleware

log = logging.getLogger(__name__)
ERROR_TEXT = "Что-то пошло не так. Ошибка записана в лог, попробуйте ещё раз."


def build_dispatcher(app) -> Dispatcher:
    """Цепочка: доступ (первым) → защита от повторов → команды/кнопки меню → колбэки → медиа → текст."""
    dp = Dispatcher()
    dp["app"] = app
    dp.update.outer_middleware(AccessMiddleware(app))
    dp.update.outer_middleware(DedupeMiddleware(app))
    dp.include_routers(*(m.router.build() for m in (commands, callbacks, media, text)))
    dp.errors.register(_on_error)
    return dp


async def _on_error(event: ErrorEvent, app) -> bool:
    log.error("Ошибка при обработке апдейта", exc_info=event.exception)
    detail = getattr(event.exception, "user_message_ru", None) or getattr(event.exception, "message_ru", None)
    update = event.update
    chat_id = (update.message.chat.id if update.message else
               update.callback_query.from_user.id if update.callback_query else None)
    if chat_id is not None:
        try:
            await app.notifier.send(chat_id, mask_secrets(f"{ERROR_TEXT}\n{detail}" if detail else ERROR_TEXT), [])
        except Exception:
            log.exception("Не удалось сообщить об ошибке пользователю")
    return True


__all__ = ["build_dispatcher"]
