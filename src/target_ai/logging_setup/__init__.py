"""Настройка логирования с маскировкой секретов."""

from __future__ import annotations

import logging
import sys

from .masking import SecretMaskingFilter, mask_secrets

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_HANDLER_FLAG = "_target_ai_handler"
_QUIET_LOGGERS = ("httpx", "httpcore")

__all__ = ["mask_secrets", "setup_logging"]


def setup_logging(level: str = "INFO") -> None:
    """Идемпотентно настраивает корневой логгер; URL-логи httpx отключены."""
    numeric = logging.getLevelName(level.upper())
    if not isinstance(numeric, int):
        raise ValueError(f"Неизвестный уровень логирования: {level!r}")
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_FLAG, False)]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, _HANDLER_FLAG, True)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(SecretMaskingFilter())
    root.addHandler(handler)
    root.setLevel(numeric)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
