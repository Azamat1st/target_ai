"""Свободный текст: ожидаемый ответ → сервисы «до запуска» → разбор намерения. Деньги — только кнопкой."""

from __future__ import annotations

import asyncio
import logging

from aiogram import F
from aiogram.types import Message

from target_ai.agents import IntentContext, IntentKind, parse_intent
from target_ai.llm.errors import LLMError
from target_ai.storage import Company

from . import actions, pending
from .routes import Routes
from .menu import UNKNOWN_TEXT
from target_ai.config.runtime import DEFAULT_HALTED, SETTING_HALTED

log = logging.getLogger(__name__)
router = Routes("text")


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message, app) -> None:
    chat_id, text = message.chat.id, message.text or ""
    state = await pending.get_pending(app, chat_id)
    if state is not None:
        await answer_pending(app, chat_id, state, text)
        return
    if await app.intake.handle_text(chat_id, text):
        return
    await route_intent(app, chat_id, text)


async def answer_pending(app, chat_id: int, state: str, text: str) -> None:
    if state.startswith(pending.SETTING):
        reply = await app.settings_service.update(state.removeprefix(pending.SETTING), text)
    elif state.startswith(pending.LIMIT):
        reply = await app.companies.set_limit(int(state.removeprefix(pending.LIMIT)), text)
    else:
        await pending.clear_pending(app, chat_id)
        return
    if reply.status != "error":  # при ошибке ввода ждём исправленное значение
        await pending.clear_pending(app, chat_id)
    await actions.say_reply(app, chat_id, reply)


async def route_intent(app, chat_id: int, text: str) -> None:
    companies = await asyncio.to_thread(app.db.companies.list)
    try:
        intent = await parse_intent(app.llm, text, IntentContext(companies=tuple(c.name for c in companies)))
    except LLMError as exc:
        log.info("Разбор намерения недоступен: %s", exc)
        await actions.say(app, chat_id, UNKNOWN_TEXT)
        return
    kind = intent.kind
    if kind is IntentKind.HALT:
        await actions.stop_all(app, chat_id)
    elif kind is IntentKind.REPORT:
        await report_intent(app, chat_id, companies, intent.company)
    elif kind is IntentKind.SETTING:
        await actions.show_settings(app, chat_id)
    elif kind in (IntentKind.PAUSE, IntentKind.RESUME, IntentKind.SET_LIMIT):
        await company_intent(app, chat_id, companies, intent)
    else:
        await actions.say(app, chat_id, UNKNOWN_TEXT)


def match_companies(companies: list[Company], hint: str | None) -> list[Company]:
    """Компания по названию или нику; без подсказки — единственная, если она одна."""
    if not hint:
        return list(companies) if len(companies) == 1 else []
    needle = hint.strip().lstrip("@").casefold()
    return [c for c in companies if needle in c.name.casefold() or needle == (c.instagram_handle or "").casefold()]


async def report_intent(app, chat_id: int, companies: list[Company], hint: str | None) -> None:
    found = match_companies(companies, hint)
    await actions.show_report(app, chat_id, found[0].id if len(found) == 1 else None)


async def company_intent(app, chat_id: int, companies: list[Company], intent) -> None:
    kind = intent.kind
    if kind is IntentKind.RESUME and not intent.company and \
            await asyncio.to_thread(app.db.settings.get, SETTING_HALTED, DEFAULT_HALTED):
        await actions.ask_resume_all(app, chat_id)
        return
    prefix = {IntentKind.PAUSE: "cmp:pause", IntentKind.RESUME: "cmp:resume", IntentKind.SET_LIMIT: "cmp:limit"}[kind]
    found = match_companies(companies, intent.company)
    if len(found) != 1:
        await actions.company_choice(app, chat_id, found, intent.company, prefix)
    elif kind is IntentKind.PAUSE:
        await actions.say_reply(app, chat_id, await app.companies.pause(found[0].id))
    elif kind is IntentKind.RESUME:
        await actions.say_reply(app, chat_id, await app.companies.resume(found[0].id))
    elif intent.amount_text:  # сначала всегда «Всё верно» на сумму — деньги не меняются от текста
        await actions.say_reply(app, chat_id, await app.companies.set_limit(found[0].id, intent.amount_text))
    else:
        await actions.ask_limit(app, chat_id, found[0].id)
