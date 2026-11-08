"""Фильтр маскировки секретов в логах (спека §12)."""

from __future__ import annotations

import logging
import re

MASK = "***"
_PATTERNS = (
    re.compile(r"EAA[A-Za-z0-9]{8,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\d{8,10}:[\w-]{35}"),
    re.compile(r"(?i)(access_token|appsecret_proof)=[^&\s\"']+"),
)


def mask_secrets(text: str) -> str:
    result = text
    for pattern in _PATTERNS:
        if pattern.groups:
            result = pattern.sub(lambda m: f"{m.group(1)}={MASK}", result)
        else:
            result = pattern.sub(MASK, result)
    return result


class SecretMaskingFilter(logging.Filter):
    """Вешается на обработчик, чтобы покрыть записи всех логгеров."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_secrets(record.getMessage())
        record.args = None
        if record.stack_info:
            record.stack_info = mask_secrets(record.stack_info)
        if record.exc_info:
            formatted = logging.Formatter().formatException(record.exc_info)
            record.exc_text = mask_secrets(formatted)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = mask_secrets(record.exc_text)
        return True
