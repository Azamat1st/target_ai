"""Исключения Исполнителя."""

from __future__ import annotations


class ExecutorError(Exception):
    """Базовая ошибка Исполнителя; текст — на русском, его можно показывать пользователю."""

    def __init__(self, message_ru: str) -> None:
        super().__init__(message_ru)
        self.message_ru = message_ru


class PolicyDenied(ExecutorError):
    """Политика отказала в действии; `reason_ru` — причина, `code` — код политики."""

    def __init__(self, reason_ru: str, code: str = "") -> None:
        super().__init__(reason_ru)
        self.reason_ru = reason_ru
        self.code = code


class ApprovalInvalid(ExecutorError):
    """Подтверждение не найдено, уже использовано, просрочено или относится к другому действию."""


class StepFailed(ExecutorError):
    """Шаг саги не выполнен (внутреннее; наружу уходит как статус `awaiting_decision`)."""

    def __init__(self, step: str, message_ru: str, fbtrace_id: str | None = None) -> None:
        super().__init__(message_ru)
        self.step = step
        self.fbtrace_id = fbtrace_id


class ApprovalRequired(ExecutorError):
    """Нужна кнопка подтверждения; `needs` — решение политики (kind, summary_ru, key)."""

    def __init__(self, needs) -> None:  # needs: policy.NeedsApproval
        super().__init__(needs.summary_ru)
        self.needs = needs
