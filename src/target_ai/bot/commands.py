"""Команды и кнопки постоянного меню."""

from __future__ import annotations

from aiogram import F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from . import actions
from .routes import Routes
from .menu import (
    ALL_BUTTONS, BTN_ADD, BTN_CHECK, BTN_COMPANIES, BTN_LOG, BTN_REPORT, BTN_SETTINGS, BTN_STOP, DEMO_BUTTONS,
)

router = Routes("commands")
REWIND_USAGE = "Напишите, на сколько дней перемотать: /rewind 7 (от 1 до 31). Работает только в демо."


@router.message(Command("start"))
async def on_start(message: Message, app) -> None:
    await actions.show_start(app, message.chat.id)


@router.message(Command("help"))
async def on_help(message: Message, app) -> None:
    await actions.show_help(app, message.chat.id)


@router.message(Command("companies"))
async def on_companies(message: Message, app) -> None:
    await actions.show_companies(app, message.chat.id)


@router.message(Command("add"))
async def on_add(message: Message, app) -> None:
    await actions.start_add(app, message.chat.id)


@router.message(Command("report"))
async def on_report(message: Message, app) -> None:
    await actions.show_report(app, message.chat.id)


@router.message(Command("log"))
async def on_log(message: Message, app) -> None:
    await actions.show_log(app, message.chat.id)


@router.message(Command("settings"))
async def on_settings(message: Message, app) -> None:
    await actions.show_settings(app, message.chat.id)


@router.message(Command("check"))
async def on_check(message: Message, app) -> None:
    await actions.run_check(app, message.chat.id)


@router.message(Command("stop"))
async def on_stop(message: Message, app) -> None:
    await actions.stop_all(app, message.chat.id)


@router.message(Command("resume_all"))
async def on_resume_all(message: Message, app) -> None:
    await actions.ask_resume_all(app, message.chat.id)


@router.message(Command("level"))
async def on_level(message: Message, app, command: CommandObject) -> None:
    await actions.show_level(app, message.chat.id, command.args)


@router.message(Command("rewind"))
async def on_rewind(message: Message, app, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await actions.say(app, message.chat.id, REWIND_USAGE)
        return
    await actions.rewind(app, message.chat.id, int(arg))


MENU_HANDLERS = {
    BTN_COMPANIES: actions.show_companies, BTN_ADD: actions.start_add, BTN_REPORT: actions.show_report,
    BTN_LOG: actions.show_log, BTN_SETTINGS: actions.show_settings, BTN_CHECK: actions.run_check,
    BTN_STOP: actions.stop_all,
}


@router.message(F.text.in_(ALL_BUTTONS))
async def on_menu_button(message: Message, app) -> None:
    text = message.text or ""
    if text in DEMO_BUTTONS:
        await actions.rewind(app, message.chat.id, DEMO_BUTTONS[text])
        return
    await MENU_HANDLERS[text](app, message.chat.id)

