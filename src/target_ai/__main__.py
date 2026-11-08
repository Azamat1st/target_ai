"""`python -m target_ai`: запуск бота на long polling."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.utils.token import TokenValidationError

from target_ai.app import App, AppConfigError
from target_ai.config import Settings, load_settings
from target_ai.logging_setup import setup_logging

logger = logging.getLogger("target_ai")
EXIT_CONFIG = 2
BAD_TOKEN_TEXT = ("Telegram не принял токен бота — проверьте TELEGRAM_BOT_TOKEN в .env "
                  "(токен выдаёт @BotFather), затем запустите бота снова.")


def check_settings(settings: Settings) -> str | None:
    """Что вписать в `.env`, чтобы бот заработал; None — всё на месте."""
    if settings.telegram_bot_token is None:
        return ("Не задан токен бота. Откройте файл .env и впишите TELEGRAM_BOT_TOKEN "
                "(токен выдаёт @BotFather в Telegram), затем запустите бота снова.")
    if not settings.telegram_allowed_ids:
        return ("Не задан список разрешённых Telegram ID. Откройте файл .env и впишите TELEGRAM_ALLOWED_IDS "
                "(ваш числовой ID можно узнать у @userinfobot), затем запустите бота снова.")
    return None


def main() -> int:
    try:
        settings = load_settings()
    except ValueError as exc:
        sys.stderr.write(f"Не удалось прочитать настройки: {exc}\n")
        return EXIT_CONFIG
    setup_logging(settings.log_level)
    problem = check_settings(settings)
    if problem is not None:
        sys.stderr.write(problem + "\n")
        return EXIT_CONFIG
    try:
        app = App.create(settings)
    except AppConfigError as exc:
        sys.stderr.write(f"{exc}\n")
        return EXIT_CONFIG
    except TokenValidationError:
        sys.stderr.write(BAD_TOKEN_TEXT + "\n")
        return EXIT_CONFIG
    try:
        asyncio.run(app.run())
    except TelegramUnauthorizedError:
        sys.stderr.write(BAD_TOKEN_TEXT + "\n")
        return EXIT_CONFIG
    except KeyboardInterrupt:
        logger.info("Остановлено по Ctrl+C.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
