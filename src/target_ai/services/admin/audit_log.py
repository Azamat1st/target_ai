"""Журнал действий (история 67): постранично, фильтр по компании, строки по-русски."""

from __future__ import annotations

from dataclasses import dataclass

from target_ai.clock import to_account_time
from target_ai.config import Settings
from target_ai.notify import Buttons
from target_ai.storage import AuditEntry, Database

from .common import io
from target_ai.config.runtime import SETTING_REPORT_TZ

CALLBACK = "log"  # log:page:<номер с 0>:<id компании или 0>
PAGE_SIZE = 10
ACTORS = {"user": "Я", "system": "Система", "optimizer": "Оптимизатор"}
OUTCOMES = {"ok": "", "error": " — ошибка", "denied": " — отказано", "intent": " — начато"}
ACTIONS = {
    "halt_all": "Стоп-кран", "resume_system": "Система возобновлена", "pause": "Пауза", "resume": "Возобновление",
    "set_monthly_limit": "Месячный лимит", "level_change": "Уровень работы", "setting_change": "Настройка",
    "mode_change": "Режим компании", "launch": "Запуск", "rollback": "Откат запуска",
    "creative_fix": "Исправление объявления", "set_budget": "Бюджет", "pause_group": "Пауза группы",
    "pause_ad": "Пауза объявления", "scale": "Масштабирование", "access_denied": "Отказ в доступе",
}
DETAIL_KEYS = ("summary", "reason", "message", "reason_ru", "new_limit", "object")
SYSTEM_TITLE = "Вся система"


@dataclass(frozen=True)
class AuditPage:
    lines: tuple[str, ...]
    page: int
    has_next: bool
    company_id: int | None
    buttons: Buttons

    @property
    def text(self) -> str:
        if not self.lines:
            return "Журнал пока пуст."
        return f"Журнал, страница {self.page + 1}\n\n" + "\n".join(self.lines)


class AuditLog:
    def __init__(self, db: Database, settings: Settings) -> None:
        self._db, self._settings = db, settings

    async def page(self, page: int = 0, company_id: int | None = None) -> AuditPage:
        page = max(page, 0)
        rows, names, tz = await io(self._load, page, company_id)
        window = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        lines = tuple(format_entry(row, names, tz) for row in window)
        has_next = len(rows) > (page + 1) * PAGE_SIZE
        return AuditPage(lines, page, has_next, company_id, self._buttons(page, has_next, company_id))

    def _load(self, page: int, company_id: int | None):
        filters = {} if company_id is None else {"company_id": company_id}
        rows = self._db.audit.list(order_by="-id", limit=(page + 1) * PAGE_SIZE + 1, **filters)
        names = {c.id: c.name for c in self._db.companies.list()}
        tz = self._db.settings.get(SETTING_REPORT_TZ, self._settings.report_tz)
        return rows, names, tz

    @staticmethod
    def _buttons(page: int, has_next: bool, company_id: int | None) -> Buttons:
        scope = company_id or 0
        row = []
        if page > 0:
            row.append(("← Новее", f"{CALLBACK}:page:{page - 1}:{scope}"))
        if has_next:
            row.append(("Старше →", f"{CALLBACK}:page:{page + 1}:{scope}"))
        return [row] if row else []


def format_entry(entry: AuditEntry, names: dict[int, str], tz: str) -> str:
    when = to_account_time(entry.created_at, tz).strftime("%d.%m %H:%M") if entry.created_at else "—"
    actor = ACTORS.get(entry.actor, entry.actor)
    company = names.get(entry.company_id, SYSTEM_TITLE) if entry.company_id is not None else SYSTEM_TITLE
    label = ACTIONS.get(entry.action, entry.action.replace("check:", "Проверка: "))
    detail = next((str(entry.data[k]) for k in DETAIL_KEYS if entry.data.get(k)), "")
    outcome = OUTCOMES.get(str(entry.data.get("outcome", "ok")), "")
    body = f"{label}: {detail}" if detail else label
    return f"{when} · {actor} · {company} · {body}{outcome}"
