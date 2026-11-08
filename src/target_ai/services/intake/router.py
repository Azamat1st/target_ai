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
from .planning_ui import QUESTION_STATES
from .texts import parse_callback

log = logging.getLogger(__name__)

_ADD_COMPANY = re.compile(r"^\s*(добав\w*|созда\w*|нов\w+)\s+(?!факт)", re.IGNORECASE)
# внутри открытого диалога «новой анкетой» считается только явное «добавь …» (ответ «Новый Уренгой» — это гео)
_ADD_COMPANY_IN_DIALOG = re.compile(r"^\s*(добав\w*|созда\w*)\s+(?!факт)", re.IGNORECASE)
_ADD_FACT = re.compile(r"^\s*(добав\w*\s+)?факт\s*[:\-—]?\s*(.*)$", re.IGNORECASE | re.DOTALL)
_NEW_PLAN = re.compile(r"^\s*(?:(?:составь|сделай|создай|построй|подготовь)\s+)?нов\w+\s+(?:медиаплан|план)\w*"
                       r"(?:\s+для\s+(?P<name>.+?))?\s*$", re.IGNORECASE)
_COMPANIES = re.compile(r"^\s*(мои\s+)?компани[июя]\w*\s*$", re.IGNORECASE)
_URL_LIKE = re.compile(r"^https?://", re.IGNORECASE)
SHORT_ANSWER = 40
INTERRUPTING = (IntentKind.REPORT, IntentKind.HALT, IntentKind.SETTING, IntentKind.ADD_COMPANY)
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
            if _ADD_COMPANY_IN_DIALOG.match(text) and not _NEW_PLAN.match(text):  # явное «добавь …» всегда начинает новую анкету
                await self.onboarding.start(chat_id, text)
                return True
            if dialog.state in QUESTION_STATES and await self.is_other_request(text):
                await asyncio.to_thread(self._db.dialogs.delete, chat_id)
                return await self._free_text(chat_id, text)
            if dialog.state == "onb":
                return await self.onboarding.handle_text(chat_id, text)
            if dialog.state == STATE_SCREENS:
                await self._notifier.send(chat_id, "Пришлите скриншот профиля картинкой или нажмите «Пропустить».", [])
                return True
            if dialog.state.startswith("plan:"):
                return await self.planning.handle_text(chat_id, text)
        return await self._free_text(chat_id, text)

    async def is_other_request(self, text: str) -> bool:
        """R18.1: ответ на вопрос-ожидание или явно другой запрос (команда, «отчёт», «компании», «новый медиаплан»)."""
        body = text.strip()
        if _NEW_PLAN.match(body) or _ADD_COMPANY_IN_DIALOG.match(body) or _COMPANIES.match(body):
            return True
        if _URL_LIKE.match(body) or (len(body) <= SHORT_ANSWER and any(ch.isdigit() for ch in body)):
            return False  # похоже на ответ: ссылка или сумма
        try:
            intent = await parse_intent(self._llm, body)
        except LLMError:
            return False
        return intent.kind in INTERRUPTING

    async def reset(self, chat_id: int) -> None:
        """/start: незаконченная анкета, правка плана и вопрос-ожидание снимаются."""
        await asyncio.to_thread(self._db.dialogs.delete, chat_id)

    async def new_plan(self, company_id: int, chat_id: int) -> None:
        """Кнопка «🧭 Новый медиаплан»: новый план с учётом истории компании (старая кампания снимется при запуске)."""
        await self.planning.propose(company_id, chat_id)

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
        companies = await asyncio.to_thread(self._db.companies.list)
        new_plan = _NEW_PLAN.match(text)
        if new_plan:
            return await self._new_plan_for(chat_id, companies, new_plan.group("name"))
        if _ADD_COMPANY.match(text):
            await self.onboarding.start(chat_id, text)
            return True
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

    async def _new_plan_for(self, chat_id: int, companies, hint: str | None) -> bool:
        needle = (hint or "").strip().lstrip("@").casefold()
        if needle:
            found = [c for c in companies if needle in c.name.casefold() or needle == (c.instagram_handle or "").casefold()]
        else:  # без названия: единственная компания или выбранная в диалоге
            chosen = set(await asyncio.to_thread(current_company_ids, self._db, chat_id))
            found = [c for c in companies if c.id in chosen] if len(chosen) == 1 else []
        if len(found) != 1:
            await self._notifier.send(chat_id, "Для какой компании? Напишите: «новый медиаплан для <название>».", [])
            return True
        await self.planning.propose(found[0].id, chat_id)
        return True

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
