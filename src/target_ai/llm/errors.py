"""Ошибки шлюза к модели. Тексты — на русском, без секретов."""


class LLMError(Exception):
    """Базовая ошибка шлюза; `user_message_ru` можно показывать пользователю."""

    user_message_ru = "Не удалось обратиться к модели."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.user_message_ru)
        if message:
            self.user_message_ru = message


class LLMRefused(LLMError):
    """Модель (или классификатор) отказалась отвечать, цепочка fallback тоже."""

    user_message_ru = "Модель отказалась обработать этот запрос."

    def __init__(self, category: str | None = None, message: str | None = None):
        super().__init__(message)
        self.category = category


class LLMSchemaError(LLMError):
    """Ответ не соответствует схеме даже после повтора (или обрезан)."""

    user_message_ru = "Модель вернула ответ в неверном формате."


class LLMRequestError(LLMError):
    """Запрос отклонён (400/401/403): повтор не поможет, нужно исправить настройки."""

    user_message_ru = "Запрос к модели отклонён."

    def __init__(self, status: int | None = None, message: str | None = None):
        super().__init__(message)
        self.status = status


class LLMUnavailable(LLMError):
    """Лимиты, перегрузка или сеть: повторы исчерпаны."""

    user_message_ru = "Модель сейчас недоступна, попробуйте позже."


class LLMConfigError(LLMError):
    """Нет ключа Claude для ClaudeLLM."""

    user_message_ru = "Не задан ключ Claude (ANTHROPIC_API_KEY)."


class ScriptExhausted(LLMError):
    """ScriptedLLM: для вызова не осталось заготовленного ответа."""
