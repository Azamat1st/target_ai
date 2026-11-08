"""OfflineLLM: детерминированные ответы без сети; схемы обслуживает agents.offline."""

from collections.abc import Sequence
from typing import Any

from .envelope import extract_task_input, extract_untrusted
from .errors import LLMError
from .protocol import ModelT, Tier, UserContent

DEMO_MARK = "(демо без Claude)"


class OfflineLLM:
    """Ничего не знает о схемах: делегирует обработчикам из `agents.offline`."""

    async def structured(
        self,
        tier: Tier,
        system: str,
        user_content: UserContent,
        schema: type[ModelT],
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> ModelT:
        from target_ai.agents.offline import HANDLERS  # ленивый импорт: нет цикла llm <-> agents

        handler = HANDLERS.get(schema)
        if handler is None:
            raise LLMError(f"Офлайн-режим не умеет схему {schema.__name__}.")
        task = {**extract_task_input(user_content), "_untrusted": extract_untrusted(user_content)}
        return handler(task)  # type: ignore[return-value]
