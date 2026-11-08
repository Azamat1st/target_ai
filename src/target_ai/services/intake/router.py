"""Фасад «до запуска»: единая точка входа для бота (таск 09) — текст, кнопки, медиа."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from target_ai.agents import IntentContext, IntentKind, parse_intent
from target_ai.llm.errors import LLMError

from .analysis import STATE_SCREENS, Analysis
from .media import MediaIntake, current_company_ids
from .onboarding import KIND_CHECK, Onboarding
from .planning import Planning
from .texts import parse_callback

log = logging.getLogger(__name__)

_ADD_COMPANY = re.compile(r"^\s*(добав\w*|созда\w*|нов\w+)\s+(?!факт)", re.IGNORECASE)
_ADD_FACT = re.compile(r"^\s*(добав\w*\s+)?факт\s*[:\-—]?\s*(.*)$", re.IGNORECASE | re.DOTALL)
CheckHook = Callable[[int], Awaitable[None]]


class Intake:
    """`Intake(db, gateway_factory, llm, clock, notifier, executor, media_dir=…, check_connection=…)`."""

    def __init__(self, db, gateway_factory, llm, clock, notifier, executor, *, media_dir: Path,
                 check_connection: CheckHook | None = None) -> None:
        self._db, self._llm, self._clock, self._notifier = db, llm, clock, notifier
        self._check = check_connection
        self.analysis = Analysis(db, gateway_factory, llm, clock, notifier, media_dir=media_dir)
        self.planning = Planning(db, gateway_factory, llm, clock, notifier, executor, media_dir=media_dir)
        self.onboarding = Onboarding(db, gateway_factory, llm, clock, notifier, on_created=self.analysis.start)
        self.media = MediaIntake(db, clock, notifier, media_dir=media_dir)

    async def start_add_company(self, chat_id: int) -> None:
        """Кнопка «➕ Компания» и команда /add."""
        await self.onboarding.start(chat_id)

    async def handle_text(self, chat_id: int, text: str) -> bool:
        """True — сообщение обработано сервисами «до запуска»; False — пусть разбирает бот."""
        dialog = await asyncio.to_thread(self._db.dialogs.get, chat_id)
        if dialog is not None:
            if dialog.state == "onb":
                return await self.onboarding.handle_text(chat_id, text)
            if dialog.state == STATE_SCREENS:
                await self._notifier.send(chat_id, "Пришлите скриншот профиля картинкой или нажмите «Пропустить».", [])
                return True
            if dialog.state.startswith("plan:"):
                return await self.planning.handle_text(chat_id, text)
        return await self._free_text(chat_id, text)

    async def handle_callback(self, chat_id: int, callback_data: str) -> bool:
        """`ap:<approval_id>:<choice>`; False — колбэк не наш (другой модуль)."""
        parsed = parse_callback(callback_data)
        if parsed is None:
            return False
        approval_id, choice = parsed
        approval = await asyncio.to_thread(self._db.approvals.get, approval_id)
        if approval is None:
            return False
        if approval.kind == KIND_CHECK:
            await self._connection_check(chat_id, approval_id)
            return True
        for handler in (self.onboarding, self.analysis, self.media, self.planning):
            if await handler.handle_callback(chat_id, approval_id, choice):
                return True
        return False

    async def handle_media(self, chat_id: int, kind: str, data: bytes | None, filename: str, *,
                           size: int | None = None, caption: str | None = None) -> None:
        """Фото/видео из Telegram: скриншот для анализа или креатив компании (`kind`: image | video)."""
        if kind == "image" and data is not None and await self.analysis.receive_screenshot(chat_id, data, filename):
            return
        await self.media.receive(chat_id, kind, data, filename, size=size, caption=caption)

    # ------------------------------------------------------------------ свободный текст
    async def _free_text(self, chat_id: int, text: str) -> bool:
        fact = _ADD_FACT.match(text)
        if fact:
            return await self._add_fact(chat_id, fact.group(2))
        if _ADD_COMPANY.match(text):
            await self.onboarding.start(chat_id, text)
            return True
        companies = await asyncio.to_thread(self._db.companies.list)
        try:
            intent = await parse_intent(self._llm, text, IntentContext(companies=tuple(c.name for c in companies)))
        except LLMError as exc:
            log.info("Разбор намерения недоступен: %s", exc)
            return False
        if intent.kind is IntentKind.ADD_COMPANY:
            await self.onboarding.start(chat_id, text)
            return True
        if intent.kind is IntentKind.ADD_FACT:
            return await self._add_fact(chat_id, intent.text or "")
        if intent.kind is IntentKind.EDIT_PLAN:
            company_id = await self._current_company(chat_id)
            if company_id is not None:
                await self.planning.propose(company_id, chat_id, edit_request=intent.text or text)
                return True
        return False

    async def _add_fact(self, chat_id: int, body: str) -> bool:
        company_id = await self._current_company(chat_id)
        if company_id is None:
            await self._notifier.send(chat_id, "Сначала добавьте компанию, потом факты о ней.", [])
            return True
        await self.analysis.add_fact(company_id, chat_id, body)
        return True

    async def _current_company(self, chat_id: int) -> int | None:
        ids = await asyncio.to_thread(current_company_ids, self._db, chat_id)
        return max(ids) if ids else None

    async def _connection_check(self, chat_id: int, approval_id: str) -> None:
        await asyncio.to_thread(self._db.approvals.consume, approval_id, self._clock.now())
        if self._check is not None:
            await self._check(chat_id)
        else:
            await self._notifier.send(chat_id, "Нажмите «🔌 Проверить подключение» в меню.", [])
