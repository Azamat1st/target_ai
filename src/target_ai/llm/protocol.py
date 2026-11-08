"""Протокол шлюза к модели."""

from collections.abc import Sequence
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

Tier = Literal["strong", "cheap"]
UserContent = str | Sequence[dict[str, Any]]
ModelT = TypeVar("ModelT", bound=BaseModel)


@runtime_checkable
class LLM(Protocol):
    async def structured(
        self,
        tier: Tier,
        system: str,
        user_content: UserContent,
        schema: type[ModelT],
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> ModelT:
        """Вернуть объект `schema`; сбой — исключение из `llm.errors`."""
        ...
