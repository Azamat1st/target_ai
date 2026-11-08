"""Постоянное меню, тексты справки и разбор названий уровней."""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from target_ai.services.admin.common import level_title

BTN_COMPANIES, BTN_ADD, BTN_REPORT = "📋 Компании", "➕ Компания", "📊 Отчёт"
BTN_LOG, BTN_SETTINGS, BTN_CHECK, BTN_STOP = "📒 Журнал", "⚙️ Настройки", "🔌 Проверить подключение", "🛑 Стоп-кран"
BTN_DAY, BTN_WEEK = "⏩ +1 день", "⏩ +7 дней"
DEMO_BUTTONS = {BTN_DAY: 1, BTN_WEEK: 7}
COMMON_BUTTONS = (BTN_COMPANIES, BTN_ADD, BTN_REPORT, BTN_LOG, BTN_SETTINGS, BTN_CHECK, BTN_STOP)
ALL_BUTTONS = frozenset((*COMMON_BUTTONS, *DEMO_BUTTONS))

LEVEL_WORDS = {"demo": "demo", "демо": "demo", "test": "test", "тест": "test", "тестовый": "test",
               "live": "live", "боевой": "live", "бой": "live"}

COMMANDS_HELP = (
    "/companies — список компаний\n/add — добавить компанию\n/report — отчёт\n/log — журнал действий\n"
    "/settings — настройки\n/check — проверить подключение\n/stop — стоп-кран: остановить все кампании\n"
    "/resume_all — снять стоп-кран\n/level — уровень работы (демо, тест, боевой)\n"
    "/rewind N — перемотать демо на N дней (только демо)\n/help — эта справка"
)
UNKNOWN_TEXT = (
    "Не понял. Вот что я умею:\n"
    "• добавить компанию — «добавь кофейню @coffee_x, Берлин, 1500 евро в месяц»;\n"
    "• отчёт — «покажи отчёт за неделю»;\n"
    "• пауза и возобновление — «поставь на паузу кофейню»;\n"
    "• месячный лимит — «лимит 500 евро» (спрошу подтверждение);\n"
    "• стоп-кран — «остановить всё».\n\n" + COMMANDS_HELP
)
DEMO_ONLY_TEXT = "Перемотка времени доступна только на демо-уровне."


def main_menu(level: str) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=BTN_COMPANIES), KeyboardButton(text=BTN_ADD)],
            [KeyboardButton(text=BTN_REPORT), KeyboardButton(text=BTN_LOG)],
            [KeyboardButton(text=BTN_SETTINGS), KeyboardButton(text=BTN_CHECK)],
            [KeyboardButton(text=BTN_STOP)]]
    if level == "demo":
        rows.append([KeyboardButton(text=BTN_DAY), KeyboardButton(text=BTN_WEEK)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True,
                               input_field_placeholder=f"Уровень: {level_title(level)}")


def start_text(level: str) -> str:
    return (f"Здравствуйте! Я веду рекламу в Instagram: изучаю компанию, предлагаю медиаплан, запускаю его "
            f"после вашей кнопки и каждый день слежу за расходом.\n\nУровень работы: {level_title(level)}.\n"
            "Начните с «➕ Компания». Меню внизу — для частых действий, обычным текстом тоже можно.\n\n"
            + COMMANDS_HELP)
