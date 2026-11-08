"""Фото, видео и файлы: бот скачивает и передаёт сервису креативов."""

from __future__ import annotations

import logging

from aiogram import F
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from target_ai.services.intake import MAX_FILE_BYTES

from . import actions
from .routes import Routes

log = logging.getLogger(__name__)
router = Routes("media")
DOWNLOAD_FAILED = "Не удалось скачать файл из Telegram. Пришлите его ещё раз."


async def _download(message: Message, app, file_id: str) -> bytes | None:
    try:
        stream = await message.bot.download(file_id)
    except TelegramAPIError:
        log.exception("Не удалось скачать файл")
        await actions.say(app, message.chat.id, DOWNLOAD_FAILED)
        return None
    return stream.read() if stream is not None else None


async def _hand_over(message: Message, app, kind: str, file_id: str, filename: str, size: int | None) -> None:
    if size is not None and size > MAX_FILE_BYTES:  # Bot API отдаёт файлы только до 20 МБ
        await app.intake.handle_media(message.chat.id, kind, None, filename, size=size, caption=message.caption)
        return
    data = await _download(message, app, file_id)
    if data is not None:
        await app.intake.handle_media(message.chat.id, kind, data, filename, size=len(data), caption=message.caption)


@router.message(F.photo)
async def on_photo(message: Message, app) -> None:
    biggest = message.photo[-1]
    await _hand_over(message, app, "image", biggest.file_id, f"{biggest.file_unique_id}.jpg", biggest.file_size)


@router.message(F.video)
async def on_video(message: Message, app) -> None:
    video = message.video
    await _hand_over(message, app, "video", video.file_id, video.file_name or f"{video.file_unique_id}.mp4",
                     video.file_size)


@router.message(F.document)
async def on_document(message: Message, app) -> None:
    document, mime = message.document, message.document.mime_type or ""
    kind = "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else None
    if kind is None:
        await actions.say(app, message.chat.id, "Принимаю только фото и видео для рекламы. Пришлите картинку или ролик.")
        return
    await _hand_over(message, app, kind, document.file_id, document.file_name or document.file_unique_id,
                     document.file_size)
