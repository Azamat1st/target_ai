"""Точка входа. Пока заглушка: приложение собирается следующими тасками."""

from __future__ import annotations

import logging

from target_ai.config import load_settings
from target_ai.logging_setup import setup_logging

logger = logging.getLogger("target_ai")


def main() -> int:
    settings = load_settings()
    setup_logging(settings.log_level)
    logger.info("Приложение ещё не собрано.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
