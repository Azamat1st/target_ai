"""Протокол исходящих сообщений и фейк для тестов (без aiogram)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Кнопки: строки пар (подпись, callback_data).
Buttons = list[list[tuple[str, str]]]


class Notifier(Protocol):
    async def send(self, chat_id: int | None, text: str, buttons: Buttons | None = None) -> int: ...

    async def edit(self, chat_id: int | None, message_id: int, text: str, buttons: Buttons | None = None) -> None: ...


@dataclass
class SentMessage:
    message_id: int
    chat_id: int | None
    text: str
    buttons: Buttons


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[SentMessage] = []

    async def send(self, chat_id: int | None, text: str, buttons: Buttons | None = None) -> int:
        message_id = len(self.messages) + 1
        self.messages.append(SentMessage(message_id, chat_id, text, buttons or []))
        return message_id

    async def edit(self, chat_id: int | None, message_id: int, text: str, buttons: Buttons | None = None) -> None:
        self.messages[message_id - 1] = SentMessage(message_id, chat_id, text, buttons or [])

    @property
    def texts(self) -> list[str]:
        return [m.text for m in self.messages]
