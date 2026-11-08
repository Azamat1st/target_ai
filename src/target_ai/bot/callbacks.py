"""Колбэки кнопок: по префиксу — в нужный сервис. Бот только маршрутизирует и показывает ответ."""

from __future__ import annotations

import asyncio
import logging

from aiogram import F
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery

from target_ai.executor import ExecutorError

from . import actions, pending
from .routes import Routes
from .actions import say, say_reply

log = logging.getLogger(__name__)
router = Routes("callbacks")

STALE_TEXT = "Эта кнопка устарела или уже использована."
# Кнопки-подтверждения: после нажатия убираем клавиатуру, чтобы не жали второй раз.
FINAL_PREFIXES = ("ap", "lvl:ok", "lvl:no", "cmp:ok", "cmp:no", "sys:ok", "sys:no", "ops", "launch", "pnd")


@router.callback_query(F.data)
async def on_callback(query: CallbackQuery, app) -> None:
    chat_id = query.message.chat.id if query.message is not None else query.from_user.id
    data = query.data or ""
    try:
        await query.answer()  # Telegram ждёт ответ быстро; результат придёт сообщением
    except TelegramBadRequest:
        log.debug("Колбэк %s уже устарел в Telegram", query.id)
    if data.startswith(FINAL_PREFIXES) and query.message is not None:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            log.debug("Не удалось убрать клавиатуру у сообщения %s", query.message.message_id)
    prefix, _, rest = data.partition(":")
    handler = ROUTES.get(prefix)
    if handler is None or not await handler(app, chat_id, rest):
        await say(app, chat_id, STALE_TEXT)


# ---------------------------------------------------------------------- обработчики по префиксам
async def _approval(app, chat_id: int, rest: str) -> bool:
    return await app.intake.handle_callback(chat_id, f"ap:{rest}")


async def _level(app, chat_id: int, rest: str) -> bool:
    action, _, arg = rest.partition(":")
    if action == "show":
        await say_reply(app, chat_id, await app.levels.view())
    elif action == "set":
        await actions.level_reply(app, chat_id, await app.levels.request(arg))
    elif action == "ok":
        await actions.level_reply(app, chat_id, await app.levels.confirm(arg))
    elif action == "no":
        await say_reply(app, chat_id, await app.levels.cancel(arg))
    else:
        return False
    return True


async def _settings(app, chat_id: int, rest: str) -> bool:
    action, _, key = rest.partition(":")
    if action == "show":
        await actions.show_settings(app, chat_id)
    elif action == "edit":
        await actions.ask_setting(app, chat_id, key)
    else:
        return False
    return True


async def _companies(app, chat_id: int, rest: str) -> bool:
    action, _, arg = rest.partition(":")
    if action == "list":
        await actions.show_companies(app, chat_id)
        return True
    if action in ("ok", "no"):
        reply = await (app.companies.confirm(arg) if action == "ok" else app.companies.cancel(arg))
        await say_reply(app, chat_id, reply)
        return True
    company_arg, _, extra = arg.partition(":")
    if not company_arg.isdigit():
        return False
    company_id = int(company_arg)
    if action == "limit":
        await actions.ask_limit(app, chat_id, company_id)
        return True
    if action == "plan":
        await actions.new_plan(app, chat_id, company_id)
        return True
    calls = {"card": lambda: app.companies.card(company_id), "pause": lambda: app.companies.pause(company_id),
             "resume": lambda: app.companies.resume(company_id),
             "mode": lambda: app.companies.set_mode(company_id, extra)}
    if action not in calls:
        return False
    await say_reply(app, chat_id, await calls[action]())
    return True


async def _log(app, chat_id: int, rest: str) -> bool:
    parts = rest.split(":")
    if len(parts) != 3 or parts[0] != "page" or not (parts[1].isdigit() and parts[2].isdigit()):
        return False
    await actions.show_log(app, chat_id, int(parts[1]), int(parts[2]) or None)
    return True


async def _system(app, chat_id: int, rest: str) -> bool:
    action, _, arg = rest.partition(":")
    if action == "ok":
        await say_reply(app, chat_id, await app.system.confirm_resume(arg))
    elif action == "no":
        await say_reply(app, chat_id, await app.system.cancel(arg))
    else:
        return False
    return True


async def _ops(app, chat_id: int, rest: str) -> bool:
    action, _, approval_id = rest.partition(":")
    approval = await asyncio.to_thread(app.db.approvals.get, approval_id) if approval_id else None
    if approval is None or approval.status != "pending":
        return False
    try:
        if action == "apply" and approval.company_id is not None:
            result = await app.ops.optimization.apply_approved(approval.company_id, approval_id)
            if not result.lines and result.skipped_reason:
                await say(app, chat_id, result.skipped_reason)
        elif action == "cancel":
            await app.ops.optimization.cancel(approval_id)
            await say(app, chat_id, "Хорошо, изменения не применяю.")
        elif action == "fix":
            await say(app, chat_id, (await app.ops.moderation.apply_fix(approval_id)).message_ru)
        elif action == "skipfix":
            await asyncio.to_thread(app.db.approvals.cancel, approval_id)
            await say(app, chat_id, "Хорошо, объявление оставляю как есть.")
        else:
            return False
    except ExecutorError as exc:
        await say(app, chat_id, str(exc))
    return True


async def _launch(app, chat_id: int, rest: str) -> bool:
    action, _, launch_id = rest.partition(":")
    if action not in ("continue", "retry", "rollback") or not launch_id.isdigit():
        return False
    try:
        if action == "rollback":
            result = await app.executor.rollback_launch(int(launch_id))
        else:
            result = await app.executor.continue_launch(int(launch_id))
    except ExecutorError as exc:
        await say(app, chat_id, str(exc))
        return True
    await say(app, chat_id, result.message_ru)
    return True


async def _pending_cancel(app, chat_id: int, rest: str) -> bool:
    await pending.clear_pending(app, chat_id)
    await say(app, chat_id, "Вопрос снят. Ничего не изменено.")
    return True


ROUTES = {"pnd": _pending_cancel, "ap": _approval, "lvl": _level, "set": _settings, "cmp": _companies, "log": _log, "sys": _system,
          "ops": _ops, "launch": _launch}


