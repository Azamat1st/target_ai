"""Действия бота: одни и те же для команд, кнопок меню, колбэков и разбора текста."""

from __future__ import annotations

import asyncio

from target_ai.services.admin import Reply, SettingError
from target_ai.services.admin.common import level_title
from target_ai.storage import Company

from . import pending
from .menu import DEMO_ONLY_TEXT, LEVEL_WORDS, main_menu, start_text

LEVEL_MENU_STATUSES = ("done",)
CANCEL_PENDING = "pnd:cancel"
CANCEL_ROW = [[("✖️ Отмена", CANCEL_PENDING)]]


async def say(app, chat_id: int, text: str, buttons=None) -> None:
    await app.notifier.send(chat_id, text, buttons or [])


async def say_reply(app, chat_id: int, reply: Reply) -> None:
    await say(app, chat_id, reply.text, reply.buttons)


async def say_with_menu(app, chat_id: int, text: str) -> None:
    """Текст + постоянная клавиатура под текущий уровень (в демо в ней есть «⏩»)."""
    level = await app.levels.current()
    sender = getattr(app.notifier, "send_with_menu", None)
    if sender is None:
        await say(app, chat_id, text)
        return
    await sender(chat_id, text, main_menu(level))


async def show_start(app, chat_id: int) -> None:
    await pending.clear_pending(app, chat_id)
    await app.intake.reset(chat_id)
    await say_with_menu(app, chat_id, start_text(await app.levels.current()))


async def show_help(app, chat_id: int) -> None:
    from .menu import COMMANDS_HELP

    await say(app, chat_id, COMMANDS_HELP)


async def show_companies(app, chat_id: int) -> None:
    await pending.clear_pending(app, chat_id)
    view = await app.companies.list()
    await say(app, chat_id, view.text, view.buttons)


async def start_add(app, chat_id: int) -> None:
    await app.intake.start_add_company(chat_id)


async def show_report(app, chat_id: int, company_id: int | None = None, period: str = "day") -> None:
    await pending.clear_pending(app, chat_id)
    await say(app, chat_id, await app.ops.reporting.on_demand(company_id, period))


async def show_log(app, chat_id: int, page: int = 0, company_id: int | None = None) -> None:
    await pending.clear_pending(app, chat_id)
    result = await app.audit_log.page(page, company_id)
    await say(app, chat_id, result.text, result.buttons)


async def show_settings(app, chat_id: int) -> None:
    await pending.clear_pending(app, chat_id)
    await say_reply(app, chat_id, await app.settings_service.view())


async def ask_setting(app, chat_id: int, key: str) -> None:
    try:
        prompt = app.settings_service.prompt(key)
    except SettingError as exc:
        await say(app, chat_id, str(exc))
        return
    await pending.set_pending(app, chat_id, pending.SETTING + key)
    await say(app, chat_id, prompt + "\n\nНапишите новое значение или нажмите «✖️ Отмена».", CANCEL_ROW)


async def ask_limit(app, chat_id: int, company_id: int) -> None:
    company = await asyncio.to_thread(app.db.companies.get, company_id)
    if company is None:
        await say(app, chat_id, "Такой компании нет.")
        return
    await pending.set_pending(app, chat_id, f"{pending.LIMIT}{company_id}")
    await say(app, chat_id, f"Новый месячный лимит для «{company.name}»? Напишите сумму, например «1500 евро». "
                            "Перед изменением я спрошу подтверждение.", CANCEL_ROW)


async def new_plan(app, chat_id: int, company_id: int) -> None:
    """«🧭 Новый медиаплан»: план с учётом истории компании; прежняя кампания снимется при запуске нового."""
    await pending.clear_pending(app, chat_id)
    if await asyncio.to_thread(app.db.companies.get, company_id) is None:
        await say(app, chat_id, "Такой компании нет.")
        return
    await say(app, chat_id, "Составляю новый медиаплан с учётом того, что уже пробовали…")
    await app.intake.new_plan(company_id, chat_id)


async def run_check(app, chat_id: int) -> None:
    await pending.clear_pending(app, chat_id)
    await say(app, chat_id, "Проверяю подключение…")
    await say(app, chat_id, (await app.connection.run()).text)


async def stop_all(app, chat_id: int) -> None:
    await pending.clear_pending(app, chat_id)
    await say_reply(app, chat_id, await app.system.stop())


async def ask_resume_all(app, chat_id: int) -> None:
    await say_reply(app, chat_id, await app.system.request_resume())


async def show_level(app, chat_id: int, argument: str | None = None) -> None:
    target = LEVEL_WORDS.get((argument or "").strip().lower())
    if argument and target is None:
        await say(app, chat_id, "Такого уровня нет: выберите демо, тест или боевой.")
    elif target is None:
        await say_reply(app, chat_id, await app.levels.view())
    else:
        await level_reply(app, chat_id, await app.levels.request(target))


async def level_reply(app, chat_id: int, reply: Reply) -> None:
    """Ответ о смене уровня; при успехе заодно обновляет клавиатуру (кнопки «⏩» только в демо)."""
    if reply.status in LEVEL_MENU_STATUSES:
        await say_with_menu(app, chat_id, reply.text)
    else:
        await say_reply(app, chat_id, reply)


async def rewind(app, chat_id: int, days: int) -> None:
    """Перемотка демо: DemoTime сам шлёт одно итоговое сообщение всем ID из белого списка."""
    await pending.clear_pending(app, chat_id)
    if await app.levels.current() != "demo":
        await say(app, chat_id, DEMO_ONLY_TEXT)
        return
    await say(app, chat_id, f"⏩ Перематываю на {days} дн.… это может занять немного времени.")
    try:
        await app.demo.advance(days)
    except ValueError as exc:
        await say(app, chat_id, str(exc))


async def company_choice(app, chat_id: int, matches: list[Company], hint: str | None, callback_prefix: str) -> None:
    """Не удалось однозначно выбрать компанию: предлагаем кнопки."""
    everyone = matches or await asyncio.to_thread(app.db.companies.list)
    if not everyone:
        await say(app, chat_id, "Компаний пока нет. Нажмите «➕ Компания».")
        return
    head = f"Не нашёл компанию «{hint}». Выберите:" if hint and not matches else "К какой компании это относится?"
    await say(app, chat_id, head, [[(c.name, f"{callback_prefix}:{c.id}")] for c in everyone])


def level_label(level: str) -> str:
    return level_title(level)
