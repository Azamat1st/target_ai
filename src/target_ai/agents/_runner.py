"""Общие вспомогательные функции агентов."""

import json
from typing import Any

from target_ai.llm import task_input, wrap_untrusted


def message(payload: dict[str, Any], *untrusted: str) -> str:
    """Собирает пользовательское содержимое: данные задачи и недоверенные блоки."""
    return "\n\n".join([task_input(payload), *untrusted])


def dump(value: Any) -> Any:
    """Приводит модели (и коллекции моделей) к JSON-совместимым структурам."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: dump(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [dump(v) for v in value]
    return value


def untrusted_json(source: str, value: Any) -> str:
    return wrap_untrusted(source, json.dumps(dump(value), ensure_ascii=False, default=str))
