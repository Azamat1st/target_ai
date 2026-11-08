"""Фейковый Telegram для тестов: сессия aiogram без сети и «человек», который пишет боту.

    tg = FakeTelegram()
    app = App.create(settings, overrides={"bot_session": tg.session, ...})
    tg.bind(app)
    await tg.say(USER, "/start")           # апдейт от любого ID
    await tg.press_button(USER, "Всё верно")  # нажать инлайн-кнопку по части подписи
    tg.last(USER).text, tg.last(USER).buttons  # что бот ответил
"""

from __future__ import annotations

import io
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery, DeleteMessage, DeleteWebhook, EditMessageReplyMarkup, EditMessageText, GetFile, GetMe,
    SendMessage, TelegramMethod,
)
from aiogram.types import (
    CallbackQuery, Chat, Document, File, Message, MessageEntity, PhotoSize, Update, User, Video,
)

BOT_ID = 4242
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@dataclass
class Outgoing:
    """Сообщение бота: текст как в Telegram (HTML, экранированный), инлайн-кнопки и клавиатура меню."""

    message_id: int
    chat_id: int
    text: str
    buttons: list[list[tuple[str, str]]] = field(default_factory=list)
    menu: list[list[str]] | None = None
    parse_mode: str | None = None
    edits: int = 0

    @property
    def labels(self) -> list[str]:
        return [label for row in self.buttons for label, _ in row]

    @property
    def callbacks(self) -> list[str]:
        return [data for row in self.buttons for _, data in row]


@dataclass(frozen=True)
class CallbackAnswer:
    callback_id: str
    text: str | None
    show_alert: bool


class FakeTelegramSession(BaseSession):
    """Перехватывает исходящие запросы бота; ответы — минимально правдоподобные."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod] = []
        self.sent: list[Outgoing] = []
        self.answers: list[CallbackAnswer] = []
        self.files: dict[str, bytes] = {}
        self.webhook_deleted = 0
        self._failures: list[tuple[type, Exception]] = []
        self._next_id = 100

    # ------------------------------------------------------------------ управление из тестов
    def fail_next(self, method_type: type, error: Exception) -> None:
        """Следующий запрос этого вида упадёт с `error` (сбой сети, лимит Telegram)."""
        self._failures.append((method_type, error))

    # ------------------------------------------------------------------ BaseSession
    async def close(self) -> None:
        return None

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:
        self.requests.append(method)
        for index, (kind, error) in enumerate(self._failures):
            if isinstance(method, kind):
                del self._failures[index]
                raise error
        handler = getattr(self, f"_on_{type(method).__name__}", None)
        if handler is None:
            raise NotImplementedError(f"Фейковая сессия не знает метод {type(method).__name__}")
        return handler(method)

    async def stream_content(self, url: str, headers: dict[str, Any] | None = None, timeout: int = 30,
                             chunk_size: int = 65536, raise_for_status: bool = True) -> AsyncGenerator[bytes, None]:
        yield self.files[url.rsplit("/", 1)[-1]]

    # ------------------------------------------------------------------ методы Telegram
    def _on_SendMessage(self, method: SendMessage) -> Message:
        self._next_id += 1
        markup = method.reply_markup
        inline = [[(b.text, b.callback_data or "") for b in row] for row in getattr(markup, "inline_keyboard", [])]
        menu = [[b.text for b in row] for row in getattr(markup, "keyboard", [])] or None
        self.sent.append(Outgoing(self._next_id, int(method.chat_id), method.text, inline, menu,
                                  str(method.parse_mode) if method.parse_mode else None))
        return self._message(self._next_id, int(method.chat_id), method.text)

    def _on_EditMessageText(self, method: EditMessageText) -> Message:
        chat_id = int(method.chat_id)
        found = self._find(chat_id, method.message_id)
        if found is not None:
            found.text, found.edits = method.text, found.edits + 1
            found.buttons = [[(b.text, b.callback_data or "") for b in row]
                             for row in getattr(method.reply_markup, "inline_keyboard", [])]
        return self._message(method.message_id, chat_id, method.text)

    def _on_EditMessageReplyMarkup(self, method: EditMessageReplyMarkup) -> Message:
        chat_id = int(method.chat_id)
        found = self._find(chat_id, method.message_id)
        if found is not None:
            found.buttons = [[(b.text, b.callback_data or "") for b in row]
                             for row in getattr(method.reply_markup, "inline_keyboard", [])]
        return self._message(method.message_id, chat_id, found.text if found else "")

    def _on_AnswerCallbackQuery(self, method: AnswerCallbackQuery) -> bool:
        self.answers.append(CallbackAnswer(method.callback_query_id, method.text, bool(method.show_alert)))
        return True

    def _on_DeleteWebhook(self, method: DeleteWebhook) -> bool:
        self.webhook_deleted += 1
        return True

    def _on_DeleteMessage(self, method: DeleteMessage) -> bool:
        return True

    def _on_GetMe(self, method: GetMe) -> User:
        return User(id=BOT_ID, is_bot=True, first_name="Таргет", username="target_ai_test_bot")

    def _on_GetFile(self, method: GetFile) -> File:
        data = self.files[method.file_id]
        return File(file_id=method.file_id, file_unique_id=method.file_id, file_size=len(data),
                    file_path=f"files/{method.file_id}")

    # ------------------------------------------------------------------ внутреннее
    def _find(self, chat_id: int, message_id: int) -> Outgoing | None:
        return next((m for m in self.sent if m.chat_id == chat_id and m.message_id == message_id), None)

    @staticmethod
    def _message(message_id: int, chat_id: int, text: str) -> Message:
        return Message(message_id=message_id, date=NOW, chat=Chat(id=chat_id, type="private"), text=text,
                       from_user=User(id=BOT_ID, is_bot=True, first_name="Таргет"))


class FakeTelegram:
    """Подаёт в диспетчер апдейты от любого ID и читает ответы бота."""

    def __init__(self) -> None:
        self.session = FakeTelegramSession()
        self._dispatcher = None
        self._bot = None
        self._update_id = 1000
        self._message_id = 0

    def bind(self, app) -> FakeTelegram:
        self._dispatcher, self._bot = app.dispatcher, app.bot
        return self

    # ------------------------------------------------------------------ что видит человек
    @property
    def sent(self) -> list[Outgoing]:
        return self.session.sent

    def to(self, chat_id: int) -> list[Outgoing]:
        return [m for m in self.sent if m.chat_id == chat_id]

    def last(self, chat_id: int | None = None) -> Outgoing:
        messages = self.to(chat_id) if chat_id is not None else self.sent
        assert messages, "бот ничего не отправил"
        return messages[-1]

    def texts(self, chat_id: int | None = None) -> list[str]:
        return [m.text for m in (self.to(chat_id) if chat_id is not None else self.sent)]

    def all_text(self, chat_id: int | None = None) -> str:
        return "\n".join(self.texts(chat_id))

    @property
    def answers(self) -> list[CallbackAnswer]:
        return self.session.answers

    def clear(self) -> None:
        self.session.sent.clear()
        self.session.answers.clear()
        self.session.requests.clear()

    # ------------------------------------------------------------------ что делает человек
    async def say(self, user_id: int, text: str, *, update_id: int | None = None) -> Any:
        entities = None
        if text.startswith("/"):
            entities = [MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))]
        message = self._incoming(user_id, text=text, entities=entities)
        return await self._feed(Update(update_id=self._uid(update_id), message=message))

    async def press(self, user_id: int, data: str, *, message_id: int | None = None, update_id: int | None = None) -> Any:
        mine = [m for m in self.to(user_id)]
        target = message_id or (mine[-1].message_id if mine else 1)
        query = CallbackQuery(id=f"cb{self._uid(None)}", from_user=self._user(user_id), chat_instance="ci", data=data,
                              message=Message(message_id=target, date=NOW, chat=Chat(id=user_id, type="private"),
                                              from_user=User(id=BOT_ID, is_bot=True, first_name="Таргет")))
        return await self._feed(Update(update_id=self._uid(update_id), callback_query=query))

    async def press_button(self, user_id: int, label_part: str, *, update_id: int | None = None) -> Any:
        """Нажимает самую свежую кнопку, в подписи которой есть `label_part`."""
        for message in reversed(self.to(user_id)):
            for label, data in (pair for row in message.buttons for pair in row):
                if label_part in label:
                    return await self.press(user_id, data, message_id=message.message_id, update_id=update_id)
        raise AssertionError(f"нет кнопки «{label_part}»; есть: {[m.labels for m in self.to(user_id)[-3:]]}")

    async def send_photo(self, user_id: int, data: bytes, *, caption: str | None = None) -> Any:
        file_id = f"photo{len(self.session.files) + 1}"
        self.session.files[file_id] = data
        sizes = [PhotoSize(file_id=file_id, file_unique_id=file_id, width=90, height=90, file_size=len(data))]
        return await self._feed(Update(update_id=self._uid(None),
                                       message=self._incoming(user_id, photo=sizes, caption=caption)))

    async def send_video(self, user_id: int, data: bytes, *, declared_size: int | None = None,
                         caption: str | None = None) -> Any:
        file_id = f"video{len(self.session.files) + 1}"
        self.session.files[file_id] = data
        video = Video(file_id=file_id, file_unique_id=file_id, width=720, height=1280, duration=5,
                      file_size=declared_size if declared_size is not None else len(data))
        return await self._feed(Update(update_id=self._uid(None),
                                       message=self._incoming(user_id, video=video, caption=caption)))

    async def send_document(self, user_id: int, data: bytes, filename: str, mime_type: str) -> Any:
        file_id = f"doc{len(self.session.files) + 1}"
        self.session.files[file_id] = data
        document = Document(file_id=file_id, file_unique_id=file_id, file_name=filename, mime_type=mime_type,
                            file_size=len(data))
        return await self._feed(Update(update_id=self._uid(None), message=self._incoming(user_id, document=document)))

    # ------------------------------------------------------------------ внутреннее
    def _uid(self, update_id: int | None) -> int:
        if update_id is not None:
            return update_id
        self._update_id += 1
        return self._update_id

    @staticmethod
    def _user(user_id: int) -> User:
        return User(id=user_id, is_bot=False, first_name="Тестовый", username=f"user{user_id}")

    def _incoming(self, user_id: int, **fields: Any) -> Message:
        self._message_id += 1
        return Message(message_id=self._message_id, date=NOW, chat=Chat(id=user_id, type="private"),
                       from_user=self._user(user_id), **fields)

    async def _feed(self, update: Update) -> Any:
        assert self._dispatcher is not None, "сначала tg.bind(app)"
        return await self._dispatcher.feed_update(self._bot, update)


def png_bytes() -> bytes:
    """Крошечный файл-«картинка» для тестов загрузки."""
    return io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 32).getvalue()
