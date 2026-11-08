"""Исключения хранилища."""


class StorageError(Exception):
    """Базовая ошибка хранилища."""


class NotFound(StorageError):
    """Запись не найдена."""


class AlreadyLaunched(StorageError):
    """Для этого медиаплана запуск уже создан."""

    def __init__(self, plan_id: int) -> None:
        super().__init__(f"Медиаплан {plan_id} уже запускался: повторный запуск невозможен.")
        self.plan_id = plan_id
