"""Разбор свободного текста пользователя (R77)."""

from target_ai.llm import LLM

from ._runner import dump, message
from .inputs import IntentContext
from .prompts import INTENT
from .schemas import Intent


async def parse_intent(llm: LLM, text: str, context: IntentContext | None = None) -> Intent:
    payload = {"user_text": text, "context": dump(context or IntentContext())}
    return await llm.structured("cheap", INTENT, message(payload), Intent)
