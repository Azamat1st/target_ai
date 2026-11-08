"""ScriptedLLM: очередь заготовленных ответов для тестов (в т.ч. вредных)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from .errors import ScriptExhausted
from .protocol import ModelT, Tier, UserContent


@dataclass(frozen=True)
class Call:
    tier: str
    system: str
    user_content: Any
    schema: type[BaseModel]
    tools: tuple[dict[str, Any], ...] | None


class ScriptedLLM:
    """`responses` — последовательность объектов-схем и исключений либо
    словарь `{схема: [ответы]}`. Объект отдаётся на вызов с его схемой (или
    подклассом), исключение без схемы — на ближайший вызов любой схемы.
    Объекты можно строить через `model_construct`, чтобы отдать заведомо вредный ответ.
    """

    def __init__(self, responses: Sequence[Any] | Mapping[type[BaseModel], Sequence[Any]] = ()):
        entries: list[tuple[type | None, Any]] = []
        if isinstance(responses, Mapping):
            for schema, items in responses.items():
                entries.extend((schema, item) for item in items)
        else:
            for item in responses:
                entries.append((None if isinstance(item, BaseException) else type(item), item))
        self._entries = entries
        self.calls: list[Call] = []

    async def structured(
        self,
        tier: Tier,
        system: str,
        user_content: UserContent,
        schema: type[ModelT],
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> ModelT:
        self.calls.append(Call(tier, system, user_content, schema, tuple(tools) if tools else None))
        for index, (target, item) in enumerate(self._entries):
            if target is None or issubclass(target, schema) or issubclass(schema, target):
                del self._entries[index]
                if isinstance(item, BaseException):
                    raise item
                return item
        raise ScriptExhausted(f"Нет заготовленного ответа для {schema.__name__}.")

    @property
    def remaining(self) -> int:
        return len(self._entries)
