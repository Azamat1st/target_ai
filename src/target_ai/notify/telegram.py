"""Исходящие сообщения через Telegram: экранирование HTML, разбиение ≤ 4096, инлайн-кнопки."""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Sequence

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from target_ai.services.intake.texts import split_message

from .protocol import Buttons

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096
SPLIT_LIMIT = 4000
CALLBACK_MAX_BYTES = 64
MAX_RETRY_WAIT_SECONDS = 30
NOT_MODIFIED = "message is not modified"


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def fit_chunks(text: str) -> list[str]:
    """Режет исходный текст так, чтобы каждая часть ПОСЛЕ экранирования влезла в одно сообщение."""
    result: list[str] = []
    for chunk in split_message([text], SPLIT_LIMIT) or [""]:
        result.extend(_shrink(chunk))
    return result


def _shrink(chunk: str) -> list[str]:
    if len(escape(chunk)) <= TELEGRAM_LIMIT or len(chunk) < 2:
        return [chunk]
    parts = split_message([chunk], max(len(chunk) // 2, 1))
    return [piece for part in parts for piece in _shrink(part)]


def inline_markup(buttons: Buttons | None) -> InlineKeyboardMarkup | None:
    rows = []
    for row in buttons or []:
        built = [InlineKeyboardButton(text=label, callback_data=data) for label, data in row if _valid(label, data)]
        if built:
            rows.append(built)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _valid(label: str, data: str) -> bool:
    if len(data.encode()) > CALLBACK_MAX_BYTES:  # Telegram отклонит всё сообщение целиком
        log.error("callback_data длиннее 64 байт, кнопка «%s» пропущена", label)
        return False
    return True


class TelegramNotifier:
    """`send(None, …)` — всем ID из белого списка; кнопки ставятся на последнюю часть длинного текста."""

    def __init__(self, bot: Bot, allowed_ids: Sequence[int]) -> None:
        self._bot = bot
        self._allowed = tuple(allowed_ids)

    async def send(self, chat_id: int | None, text: str, buttons: Buttons | None = None) -> int:
        if chat_id is not None:
            return await self._send_one(chat_id, text, inline_markup(buttons))
        if not self._allowed:
            log.warning("Некому отправить сообщение: список TELEGRAM_ALLOWED_IDS пуст.")
            return 0
        first = 0
        for target in self._allowed:
            try:
                message_id = await self._send_one(target, text, inline_markup(buttons))
            except TelegramAPIError:
                log.exception("Не удалось отправить сообщение в чат %s", target)
                continue
            first = first or message_id
        return first

    async def send_with_menu(self, chat_id: int, text: str, menu: ReplyKeyboardMarkup) -> int:
        """Сообщение с постоянной клавиатурой меню (инлайн-кнопки к такому сообщению не прикрепить)."""
        return await self._send_one(chat_id, text, menu)

    async def edit(self, chat_id: int | None, message_id: int, text: str, buttons: Buttons | None = None) -> None:
        target = chat_id if chat_id is not None else (self._allowed[0] if self._allowed else None)
        if target is None:
            return
        head, *rest = fit_chunks(text)
        try:
            await self._call(self._bot.edit_message_text, text=escape(head), chat_id=target, message_id=message_id,
                             parse_mode=ParseMode.HTML, reply_markup=inline_markup(buttons))
        except TelegramBadRequest as exc:
            if NOT_MODIFIED not in str(exc):
                raise
        for chunk in rest:
            await self._send_one(target, chunk, inline_markup(buttons))

    async def _send_one(self, chat_id: int, text: str, markup) -> int:
        chunks = fit_chunks(text)
        first = 0
        for index, chunk in enumerate(chunks):
            last = index == len(chunks) - 1
            message = await self._call(self._bot.send_message, chat_id=chat_id, text=escape(chunk),
                                       parse_mode=ParseMode.HTML, reply_markup=markup if last else None)
            first = first or message.message_id
        return first

    @staticmethod
    async def _call(method, **kwargs):
        try:
            return await method(**kwargs)
        except TelegramRetryAfter as exc:
            if exc.retry_after > MAX_RETRY_WAIT_SECONDS:
                raise
            await asyncio.sleep(exc.retry_after)
            return await method(**kwargs)
