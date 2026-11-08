"""Журнал действий (история 67): постранично, фильтр по компании, строки по-русски."""

from __future__ import annotations

import re
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
    "launch:start": "Запуск начат", "launch:done": "Запуск завершён", "launch:failed": "Запуск не удался",
    "launch:recheck": "Повторная проверка запуска", "launch:rollback": "Откат запуска",
    "validate_plan": "Проверка плана", "enable": "Включение", "create_ad": "Новое объявление",
}
FINISHED = ("ok", "error", "denied")
TAG_RE = re.compile(r"\s*\[ta:[^\]]*\]")
OVERFETCH = 3  # пары «начато/завершено» сворачиваются, поэтому читаем с запасом
DETAIL_KEYS = ("summary", "reason", "message", "reason_ru", "new_limit")
SUBJECT_KEYS = ("object", "target", "campaign")
CREATED = {"campaign": "Создана кампания", "adset": "Создана группа", "audience": "Создана аудитория",
           "engagement_audience": "Создана аудитория вовлечённых", "creative": "Создан креатив", "ad": "Создано объявление"}
STEP_ACTIONS = {"upload:media": "Загрузка фото или видео", "retire:pause": "Снята прежняя кампания",
                "spend_cap": "Защитный лимит кампании", "activate": "Включение"}
OBJECT_KINDS = {"campaign": "кампания", "adset": "группа", "ad": "объявление", "creative": "креатив",
                "audience": "аудитория"}
UNKNOWN_ACTION = "Служебная операция"
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
        self._objects: dict[str, tuple[str, str]] = {}  # meta_id -> (тип, имя) для строк без своего названия

    async def page(self, page: int = 0, company_id: int | None = None) -> AuditPage:
        page = max(page, 0)
        rows, names, tz = await io(self._load, page, company_id)
        window = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        lines = tuple(format_entry(row, names, tz, self._objects) for row in window)
        has_next = len(rows) > (page + 1) * PAGE_SIZE
        return AuditPage(lines, page, has_next, company_id, self._buttons(page, has_next, company_id))

    def _load(self, page: int, company_id: int | None):
        filters = {} if company_id is None else {"company_id": company_id}
        raw = self._db.audit.list(order_by="-id", limit=((page + 1) * PAGE_SIZE + 1) * OVERFETCH, **filters)
        rows = collapse_pairs(raw)
        names = {c.id: c.name for c in self._db.companies.list()}
        for obj in self._db.objects.list():
            self._objects[obj.meta_id] = (obj.kind, obj.name or "")
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


def collapse_pairs(newest_first: list[AuditEntry]) -> list[AuditEntry]:
    """Убирает «начато», у которого есть итог (ok/error/denied) того же действия: остаётся одна строка с итогом."""
    open_intents: dict[tuple, list[int]] = {}
    dropped: set[int] = set()
    for row in reversed(newest_first):
        key = (row.action, row.company_id)
        outcome = row.data.get("outcome", "ok")
        if outcome == "intent":
            open_intents.setdefault(key, []).append(row.id)
        elif outcome in FINISHED and open_intents.get(key):
            dropped.add(open_intents[key].pop(0))
    return [row for row in newest_first if row.id not in dropped]


def action_label(action: str) -> str:
    if action in ACTIONS:
        return ACTIONS[action]
    if action in STEP_ACTIONS:
        return STEP_ACTIONS[action]
    created = CREATED.get(action.split(":")[1]) if action.startswith("create:") else None
    if created:
        return created
    if action.startswith("check:"):
        return "Проверка: " + action.removeprefix("check:")
    if action.startswith("decision:"):
        return ACTIONS.get(action.removeprefix("decision:"), "Решение оптимизатора")
    return UNKNOWN_ACTION


def _subject(entry: AuditEntry, objects: dict[str, tuple[str, str]]) -> str:
    """Тип объекта и его имя без тега (по реестру); сырой ID пользователю не показывается."""
    for key in SUBJECT_KEYS:
        found = objects.get(str(entry.data.get(key) or ""))
        if found:
            kind, name = found
            title = OBJECT_KINDS.get(kind, "объект")
            clean = TAG_RE.sub("", name).strip()
            return f"{title} «{clean}»" if clean else title
    return ""


def format_entry(entry: AuditEntry, names: dict[int, str], tz: str,
                 objects: dict[str, tuple[str, str]] | None = None) -> str:
    when = to_account_time(entry.created_at, tz).strftime("%d.%m %H:%M") if entry.created_at else "—"
    actor = ACTORS.get(entry.actor, entry.actor)
    company = names.get(entry.company_id, SYSTEM_TITLE) if entry.company_id is not None else SYSTEM_TITLE
    label = action_label(entry.action)
    detail = TAG_RE.sub("", next((str(entry.data[k]) for k in DETAIL_KEYS if entry.data.get(k)), ""))
    detail = detail or _subject(entry, objects or {})
    outcome = OUTCOMES.get(str(entry.data.get("outcome", "ok")), "")
    body = f"{label}: {detail}" if detail else label
    return f"{when} · {actor} · {company} · {body}{outcome}"
