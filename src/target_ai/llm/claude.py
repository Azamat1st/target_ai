"""ClaudeLLM: structured-вызовы Claude с проверкой схемы, повторами и fallback."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import anthropic
from anthropic import AsyncAnthropic
from pydantic import ValidationError

from target_ai.config import Settings

from .errors import (
    LLMConfigError,
    LLMError,
    LLMRefused,
    LLMRequestError,
    LLMSchemaError,
    LLMUnavailable,
)
from .protocol import ModelT, Tier, UserContent

log = logging.getLogger(__name__)

EFFORT: dict[str, str] = {"strong": "high", "cheap": "low"}
FALLBACK_MODELS = frozenset({"claude-opus-5-5"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"
START_MAX_TOKENS = 12_000
MAX_TOKENS_CAP = 32_000
SCHEMA_RETRIES = 1
TRANSIENT_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 30.0
MAX_PAUSE_CONTINUATIONS = 5
REQUEST_TIMEOUT_SECONDS = 600.0

_TRANSIENT = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.APIConnectionError,  # включает таймауты
    anthropic.OverloadedError,
)
_REJECTED_TEXT = {
    400: "Запрос к модели отклонён как некорректный.",
    401: "Ключ Claude не принят: проверьте ANTHROPIC_API_KEY.",
    403: "Ключу Claude не хватает прав на этот запрос.",
}

Sleep = Callable[[float], Awaitable[None]]


class ClaudeLLM:
    def __init__(
        self,
        settings: Settings,
        *,
        client: AsyncAnthropic | None = None,
        sleep: Sleep = asyncio.sleep,
    ):
        if client is None:
            if settings.anthropic_api_key is None:
                raise LLMConfigError()
            client = AsyncAnthropic(
                api_key=settings.anthropic_api_key.get_secret_value(),
                max_retries=0,  # повторы делаем сами, с паузой и логом
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        self._client = client
        self._models = {"strong": settings.model_strong, "cheap": settings.model_cheap}
        self._sleep = sleep

    async def structured(
        self,
        tier: Tier,
        system: str,
        user_content: UserContent,
        schema: type[ModelT],
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> ModelT:
        model = self._models[tier]
        messages: list[dict[str, Any]] = [{"role": "user", "content": _content(user_content)}]
        max_tokens = START_MAX_TOKENS
        schema_failures = pauses = 0
        while True:
            params = _params(model, tier, system, messages, schema, tools, max_tokens)
            response = await self._send(model, params)
            reason = response.stop_reason
            if reason == "refusal":
                details = getattr(response, "stop_details", None)
                raise LLMRefused(getattr(details, "category", None))
            if reason == "max_tokens":
                if max_tokens >= MAX_TOKENS_CAP:
                    raise LLMSchemaError("Ответ модели обрезан по длине даже на максимальном лимите.")
                max_tokens = min(max_tokens * 2, MAX_TOKENS_CAP)
                continue
            if reason == "pause_turn":
                pauses += 1
                if pauses > MAX_PAUSE_CONTINUATIONS:
                    raise LLMError("Модель слишком долго не завершала ход.")
                messages = [*messages, {"role": "assistant", "content": response.content}]
                continue
            try:
                return schema.model_validate_json(_final_text(response))
            except (ValidationError, ValueError) as exc:
                schema_failures += 1
                log.warning("Ответ не по схеме %s (попытка %d): %s", schema.__name__, schema_failures, exc)
                if schema_failures > SCHEMA_RETRIES:
                    raise LLMSchemaError() from exc

    async def _send(self, model: str, params: dict[str, Any]) -> Any:
        for attempt in range(1, TRANSIENT_ATTEMPTS + 1):
            try:
                if model in FALLBACK_MODELS:
                    return await self._client.beta.messages.create(
                        betas=[FALLBACK_BETA], fallbacks="default", **params
                    )
                return await self._client.messages.create(**params)
            except _TRANSIENT as exc:
                if attempt == TRANSIENT_ATTEMPTS:
                    raise LLMUnavailable() from exc
                delay = _retry_delay(exc, attempt)
                log.warning("Сбой модели (%s), повтор %d через %.0f с", type(exc).__name__, attempt, delay)
                await self._sleep(delay)
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500:
                    if attempt == TRANSIENT_ATTEMPTS:
                        raise LLMUnavailable() from exc
                    await self._sleep(_retry_delay(exc, attempt))
                    continue
                text = _REJECTED_TEXT.get(exc.status_code, "Запрос к модели отклонён.")
                raise LLMRequestError(exc.status_code, text) from exc
        raise LLMUnavailable()  # недостижимо: цикл всегда возвращает или бросает


def _content(user_content: UserContent) -> str | list[dict[str, Any]]:
    return user_content if isinstance(user_content, str) else list(user_content)


def _params(
    model: str,
    tier: Tier,
    system: str,
    messages: list[dict[str, Any]],
    schema: type[ModelT],
    tools: Sequence[dict[str, Any]] | None,
    max_tokens: int,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
        "thinking": {"type": "adaptive"},
        "timeout": REQUEST_TIMEOUT_SECONDS,  # явный таймаут снимает SDK-запрет на большой max_tokens без стрима
        "output_config": {
            "effort": EFFORT[tier],
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)},
        },
    }
    if tools:
        params["tools"] = list(tools)
    return params


def _final_text(response: Any) -> str:
    texts = [b.text for b in response.content if getattr(b, "type", None) == "text" and b.text]
    if not texts:
        raise ValueError("в ответе нет текстового блока")
    return texts[-1]


def _retry_delay(exc: Exception, attempt: int) -> float:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    retry_after = headers.get("retry-after") if headers is not None else None
    try:
        if retry_after is not None:
            return min(float(retry_after), BACKOFF_CAP_SECONDS)
    except ValueError:
        pass
    return min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_CAP_SECONDS)
