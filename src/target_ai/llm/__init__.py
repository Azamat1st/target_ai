"""Шлюз к Claude (спека §5): протокол, три реализации, ошибки."""

from .claude import ClaudeLLM
from .envelope import UNTRUSTED_RULE, extract_task_input, extract_untrusted, task_input, wrap_untrusted
from .errors import (
    LLMConfigError,
    LLMError,
    LLMRefused,
    LLMRequestError,
    LLMSchemaError,
    LLMUnavailable,
    ScriptExhausted,
)
from .offline import DEMO_MARK, OfflineLLM
from .protocol import LLM, Tier, UserContent
from .scripted import Call, ScriptedLLM

__all__ = [
    "DEMO_MARK", "LLM", "UNTRUSTED_RULE", "Call", "ClaudeLLM", "LLMConfigError", "LLMError",
    "LLMRefused", "LLMRequestError", "LLMSchemaError", "LLMUnavailable", "OfflineLLM",
    "ScriptExhausted", "ScriptedLLM", "Tier", "UserContent", "extract_task_input", "extract_untrusted",
    "task_input", "wrap_untrusted",
]
