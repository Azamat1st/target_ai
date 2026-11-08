"""Настройки (история 71): правила оптимизатора, время сводки, пороги уведомлений, часовой пояс отчётов."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from target_ai.clock import Clock
from target_ai.config import Settings
from target_ai.policy import OptimizerRules
from target_ai.storage import Database

from .common import ACTOR_USER, DONE, ERROR, Journal, Reply, io

CALLBACK = "set"  # set:show · set:edit:<ключ>
SETTING_RULES = "optimizer_rules"  # те же ключи читают Исполнитель и сервисы «после запуска»
SETTING_THRESHOLDS = "alert_thresholds"
SETTING_REPORT_TIME = "report_time"
SETTING_REPORT_TZ = "report_tz"
DEFAULT_REPORT_TIME = "10:00"
MAX_HOURS = 720
TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class SettingSpec:
    key: str  # rule.<поле> · thr.<поле> · report_time · report_tz
    title_ru: str
    hint_ru: str
    kind: str  # int | decimal | time | tz
    low: Decimal | None = None
    high: Decimal | None = None
    unit: str = ""


def _spec(key, title, kind, low=None, high=None, unit="", hint=""):
    bounds = f"от {low} до {high}" if low is not None and high is not None else ""
    text = hint or {"int": f"Пришлите целое число {bounds}.", "decimal": f"Пришлите число {bounds}."}.get(kind, "")
    return SettingSpec(key, title, text, kind, None if low is None else Decimal(str(low)),
                       None if high is None else Decimal(str(high)), unit)


SPECS: tuple[SettingSpec, ...] = (
    _spec("rule.min_hours_before_decision", "Часов работы до решения", "int", 1, MAX_HOURS, "ч"),
    _spec("rule.min_spend_target_cpa_multiple", "Расход до решения (× целевая цена)", "decimal", 0.1, 100),
    _spec("rule.budget_change_max_pct", "Макс. изменение бюджета за раз", "int", 1, 100, "%"),
    _spec("rule.budget_change_min_interval_hours", "Пауза между изменениями бюджета", "int", 1, MAX_HOURS, "ч"),
    _spec("rule.kill_cpa_multiple", "Отключать, если цена выше целевой в", "decimal", 0.1, 100, "×"),
    _spec("rule.strong_cpa_ratio", "Сильная группа: цена не выше целевой ×", "decimal", 0.1, 10, "×"),
    _spec("rule.fatigue_frequency", "Усталость аудитории: частота показов", "decimal", 1, 20),
    _spec("rule.weak_ad_cpa_multiple", "Слабое объявление: цена выше средней в", "decimal", 0.1, 100, "×"),
    _spec("rule.weak_ad_min_impressions", "Слабое объявление: минимум показов", "int", 0, 1_000_000),
    _spec("thr.no_impressions_hours", "Тревога: нет показов (часов)", "int", 1, MAX_HOURS, "ч"),
    _spec("thr.spend_spike_ratio", "Тревога: всплеск расхода (×)", "decimal", 1.1, 100, "×"),
    _spec("thr.cpa_spike_ratio", "Тревога: рост цены результата (×)", "decimal", 1.1, 100, "×"),
    _spec("thr.token_days", "Предупреждать об окончании токена за (дней)", "int", 1, 90, "дн."),
    _spec("report_time", "Время ежедневной сводки", "time", hint="Пришлите время в формате ЧЧ:ММ, например 09:30."),
    _spec("report_tz", "Часовой пояс отчётов", "tz", hint="Пришлите название пояса, например Europe/Moscow."),
)
SPEC_BY_KEY = {spec.key: spec for spec in SPECS}


class SettingError(ValueError):
    """Значение не подошло; текст — на русском, его показывают пользователю."""


class SettingsService:
    def __init__(self, db: Database, clock: Clock, settings: Settings) -> None:
        self._db, self._settings = db, settings
        self._journal = Journal(db, clock)

    # ------------------------------------------------------------------ чтение
    def allowed_ids(self) -> tuple[int, ...]:
        """Список Telegram ID — только чтение: меняется в `.env`."""
        return self._settings.telegram_allowed_ids

    def current_rules(self) -> OptimizerRules:
        saved = self._db.settings.get(SETTING_RULES) or {}
        defaults = OptimizerRules()
        values = {f.name: _coerce(getattr(defaults, f.name), saved[f.name]) for f in fields(defaults) if f.name in saved}
        return OptimizerRules(**{**defaults.__dict__, **values})

    def current(self, key: str) -> Any:
        spec = self._spec(key)
        group, _, name = spec.key.partition(".")
        if group == "rule":
            return getattr(self.current_rules(), name)
        if group == "thr":
            saved = self._db.settings.get(SETTING_THRESHOLDS) or {}
            return _coerce(_THRESHOLD_DEFAULTS[name], saved.get(name, _THRESHOLD_DEFAULTS[name]))
        if key == "report_time":
            return self._db.settings.get(SETTING_REPORT_TIME, DEFAULT_REPORT_TIME)
        return self._db.settings.get(SETTING_REPORT_TZ, self._settings.report_tz)

    async def view(self) -> Reply:
        rows = await io(lambda: [(s, self.current(s.key)) for s in SPECS])
        sections = (("Правила оптимизатора", "rule."), ("Пороги уведомлений", "thr."), ("Отчёты", "report_"))
        lines = []
        for title, prefix in sections:
            lines += [title] + [f"• {s.title_ru}: {_show(v)}{(' ' + s.unit) if s.unit else ''}" for s, v in rows
                                if s.key.startswith(prefix)] + [""]
        ids = ", ".join(str(i) for i in self.allowed_ids()) or "не заданы"
        lines.append(f"Разрешённые Telegram ID (меняются только в файле .env): {ids}")
        buttons = [[(s.title_ru[:40], f"{CALLBACK}:edit:{s.key}")] for s in SPECS]
        return Reply(DONE, "\n".join(lines), buttons)

    def prompt(self, key: str) -> str:
        spec = self._spec(key)
        return f"{spec.title_ru}\nСейчас: {_show(self.current(key))}\n{spec.hint_ru}"

    # ------------------------------------------------------------------ изменение
    async def update(self, key: str, text: str, *, actor: str = ACTOR_USER) -> Reply:
        try:
            spec = self._spec(key)
            value = parse_value(spec, text)
            old = await io(self.current, key)
            await io(self._store, spec, value)
        except SettingError as exc:
            return Reply(ERROR, str(exc))
        summary = f"{spec.title_ru}: {_show(old)} → {_show(value)}"
        await self._journal.write(actor, "setting_change", None, "ok", summary=summary, key=key)
        return Reply(DONE, f"Готово. {summary}.")

    def _store(self, spec: SettingSpec, value: Any) -> None:
        group, _, name = spec.key.partition(".")
        if group == "rule":
            saved = dict(self._db.settings.get(SETTING_RULES) or {})
            saved[name] = _plain(value)
            _validate_rules(saved)
            self._db.settings.set(SETTING_RULES, saved)
        elif group == "thr":
            saved = dict(self._db.settings.get(SETTING_THRESHOLDS) or {})
            saved[name] = _plain(value)
            self._db.settings.set(SETTING_THRESHOLDS, saved)
        else:
            self._db.settings.set(SETTING_REPORT_TIME if spec.key == "report_time" else SETTING_REPORT_TZ, value)

    @staticmethod
    def _spec(key: str) -> SettingSpec:
        if key not in SPEC_BY_KEY:
            raise SettingError("Такой настройки нет.")
        return SPEC_BY_KEY[key]


_THRESHOLD_DEFAULTS: dict[str, Any] = {"no_impressions_hours": 6, "spend_spike_ratio": Decimal("1.5"),
                                       "cpa_spike_ratio": Decimal("2.0"), "token_days": 7}


def parse_value(spec: SettingSpec, text: str) -> Any:
    raw = text.strip().replace("%", "").strip()
    if spec.kind == "int":
        return _in_range(spec, _parse_int(spec, raw))
    if spec.kind == "decimal":
        return _in_range(spec, _parse_decimal(spec, raw))
    if spec.kind == "time":
        match = TIME_PATTERN.match(raw)
        if not match:
            raise SettingError(f"«{text.strip()}» — не время. Пришлите в формате ЧЧ:ММ, например 09:30.")
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    return _parse_tz(raw)


def _parse_int(spec: SettingSpec, raw: str) -> int:
    if not re.fullmatch(r"\d+", raw):
        raise SettingError(f"«{raw}» — не целое число. {spec.hint_ru}")
    return int(raw)


def _parse_decimal(spec: SettingSpec, raw: str) -> Decimal:
    try:
        value = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        raise SettingError(f"«{raw}» — не число. {spec.hint_ru}") from None
    if not value.is_finite():
        raise SettingError(f"«{raw}» — не число. {spec.hint_ru}")
    return value


def _in_range(spec: SettingSpec, value: int | Decimal) -> int | Decimal:
    if spec.low is not None and spec.high is not None and not spec.low <= value <= spec.high:
        raise SettingError(f"Значение {_show(value)} не подходит: допустимо от {_show(spec.low)} до {_show(spec.high)}.")
    return value


def _parse_tz(raw: str) -> str:
    try:
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise SettingError(f"Часовой пояс «{raw}» не найден. Пришлите название вида Europe/Moscow или Asia/Tashkent.") from None
    return raw


def _validate_rules(saved: dict[str, Any]) -> None:
    defaults = OptimizerRules()
    values = {k: _coerce(getattr(defaults, k), v) for k, v in saved.items() if hasattr(defaults, k)}
    try:
        OptimizerRules(**{**defaults.__dict__, **values})
    except ValueError as exc:
        raise SettingError(str(exc)) from None


def _coerce(default: Any, value: Any) -> Any:
    return Decimal(str(value)) if isinstance(default, Decimal) else int(value)


def _plain(value: Any) -> Any:
    return str(value) if isinstance(value, Decimal) else value


def _show(value: Any) -> str:
    if isinstance(value, Decimal):
        text = format(value.normalize(), "f")
        return text.replace(".", ",")
    return str(value)
