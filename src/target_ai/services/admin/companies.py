"""Компании: список, карточка, пауза, возобновление, месячный лимит, режим (истории 64–66, 72)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from target_ai.clock import Clock, to_account_time
from target_ai.config import Settings
from target_ai.executor import ExecutorError
from target_ai.money import Money, MoneyError, format_ru, from_major, parse_amount_ru
from target_ai.notify import Buttons
from target_ai.storage import Company, Database

from .common import (
    ACTOR_USER, DONE, ERROR, NEEDS_APPROVAL, SETTING_LEVEL, Journal, Reply, confirm_buttons, hide_meta_ids, intent_of, io,
    level_title,
)
from target_ai.config.runtime import DEFAULT_HALTED, DEFAULT_LEVEL, SETTING_HALTED, SETTING_LEVEL, SETTING_REPORT_TZ

# Колбэки: cmp:list · cmp:card:<id> · cmp:pause:<id> · cmp:resume:<id> · cmp:limit:<id> (бот спрашивает сумму)
#          cmp:mode:<id>:autonomous|confirm · cmp:ok:<подтверждение> · cmp:no:<подтверждение>
CALLBACK = "cmp"
MODE_TITLES = {"confirm": "С подтверждением", "autonomous": "Автономный"}
LIMIT_STATUS = "на паузе — лимит месяца исчерпан"
APPROVAL_MODE = "mode_autonomous"
APPROVAL_LIMIT = "limit_amount"  # «Всё верно» на сумму лимита: не денежное разрешение
OK_LABEL = "✅ Всё верно"
AUTONOMOUS_WARNING = (
    "Включить «Автономный» режим для «{name}»?\n\nЧто это значит: агент сам, без вашей кнопки, повышает бюджеты "
    "сильных групп и возобновляет остановленное — но только в пределах дневного потолка. Месячный лимит, "
    "правила оптимизатора и стоп-кран действуют всегда. Выключить режим можно в любой момент."
)


@dataclass(frozen=True)
class CompanyRow:
    id: int
    name: str
    status_ru: str
    mode: str
    spent_month: Money | None
    monthly_limit: Money | None
    is_demo: bool

    @property
    def line(self) -> str:
        money = ""
        if self.monthly_limit is not None:
            spent = format_ru(self.spent_month) if self.spent_month else "0"
            money = f" · {spent} из {format_ru(self.monthly_limit)}"
        demo = " · демо" if self.is_demo else ""
        return f"{self.name} · {self.status_ru} · {MODE_TITLES.get(self.mode, self.mode)}{money}{demo}"


@dataclass(frozen=True)
class CompaniesView:
    level: str
    rows: tuple[CompanyRow, ...]
    text: str
    buttons: Buttons


class Companies:
    def __init__(self, db: Database, clock: Clock, settings: Settings, executor) -> None:
        self._db, self._clock, self._settings, self._executor = db, clock, settings, executor
        self._journal = Journal(db, clock)

    # ------------------------------------------------------------------ чтение
    async def list(self) -> CompaniesView:
        level, rows = await io(self._load_rows)
        header = f"Уровень работы: {level_title(level)}"
        if not rows:
            return CompaniesView(level, (), f"{header}\n\nКомпаний пока нет. Нажмите «➕ Компания».", [])
        text = header + "\n\n" + "\n".join(f"{i}. {row.line}" for i, row in enumerate(rows, 1))
        buttons = [[(row.name, f"{CALLBACK}:card:{row.id}")] for row in rows]
        return CompaniesView(level, tuple(rows), text, buttons)

    async def card(self, company_id: int) -> Reply:
        found = await io(self._load_card, company_id)
        if found is None:
            return Reply(ERROR, "Такой компании нет.")
        row, company, level = found
        lines = [row.name + (" (демо)" if row.is_demo else ""), f"Уровень работы: {level_title(level)}",
                 f"Статус: {row.status_ru}", f"Режим: {MODE_TITLES.get(row.mode, row.mode)}"]
        if row.monthly_limit is not None:
            lines.append(f"Расход месяца: {format_ru(row.spent_month) if row.spent_month else '0'} из "
                         f"{format_ru(row.monthly_limit)}")
        else:
            lines.append("Месячный лимит не задан.")
        if company.instagram_handle:
            lines.append(f"Instagram: @{company.instagram_handle.lstrip('@')}")
        return Reply(DONE, "\n".join(lines), self._card_buttons(row))

    @staticmethod
    def _card_buttons(row: CompanyRow) -> Buttons:
        cid = row.id
        first = ("⏸ Пауза", f"{CALLBACK}:pause:{cid}") if row.status_ru == "Работает" \
            else ("▶️ Возобновить", f"{CALLBACK}:resume:{cid}")
        mode_to = "confirm" if row.mode == "autonomous" else "autonomous"
        return [[first, ("💰 Изменить лимит", f"{CALLBACK}:limit:{cid}")],
                [("🧭 Новый медиаплан", f"{CALLBACK}:plan:{cid}")],
                [(f"Режим → {MODE_TITLES[mode_to]}", f"{CALLBACK}:mode:{cid}:{mode_to}")],
                [("← К списку", f"{CALLBACK}:list")]]

    # ------------------------------------------------------------------ действия
    async def pause(self, company_id: int, *, actor: str = ACTOR_USER) -> Reply:
        try:
            report = await self._executor.pause_company(company_id, actor=actor)
        except ExecutorError as exc:
            return Reply(ERROR, exc.message_ru)
        return Reply(DONE, report.message_ru)

    async def resume(self, company_id: int, *, actor: str = ACTOR_USER, approval_id: str | None = None) -> Reply:
        try:
            result = await self._executor.resume_company(company_id, approval_id, actor=actor)
        except ExecutorError as exc:
            return Reply(ERROR, exc.message_ru)
        return await self._reply_for(result, company_id, {"op": "resume", "company_id": company_id, "actor": actor})

    async def set_limit(self, company_id: int, amount_text: str, *, actor: str = ACTOR_USER) -> Reply:
        company = await io(self._db.companies.get, company_id)
        if company is None:
            return Reply(ERROR, "Такой компании нет.")
        try:
            limit = self._parse_limit(company, amount_text)
        except ValueError as exc:
            return Reply(ERROR, str(exc))
        intent = {"op": "limit_amount", "company_id": company_id, "actor": actor, **_money_json(limit)}
        approval = await io(self._db.approvals.create, kind=APPROVAL_LIMIT, now=self._clock.now(),
                            company_id=company_id, payload={"intent": intent})
        text = (f"Новый месячный лимит компании «{company.name}»: {format_ru(limit)} ({limit.currency}).\n"
                f"Сейчас: {format_ru(company.monthly_limit) if company.monthly_limit else 'не задан'}.\nВсё верно?")
        return Reply(NEEDS_APPROVAL, text, confirm_buttons(CALLBACK, approval.id, OK_LABEL), approval.id)

    async def _apply_limit(self, company_id: int, limit: Money, actor: str, approval_id: str | None) -> Reply:
        try:
            result = await self._executor.set_monthly_limit(company_id, limit, approval_id, actor=actor)
        except ExecutorError as exc:
            return Reply(ERROR, exc.message_ru)
        intent = {"op": "limit", "company_id": company_id, "actor": actor, **_money_json(limit)}
        return await self._reply_for(result, company_id, intent)

    @staticmethod
    def _parse_limit(company: Company, text: str) -> Money:
        if not company.currency:
            raise ValueError("У компании не привязан рекламный аккаунт, поэтому валюта лимита неизвестна.")
        try:
            amount, currency = parse_amount_ru(text)
            if currency not in (None, company.currency):
                raise ValueError(f"Лимит задаётся в валюте рекламного аккаунта: {company.currency}.")
            limit = from_major(amount, company.currency)
        except MoneyError as exc:
            raise ValueError(f"Не получилось понять сумму: {exc}") from None
        except ValueError as exc:
            raise ValueError(str(exc) or "Не получилось понять сумму. Пример: 500 или 1 200,50.") from None
        if limit.amount_minor <= 0:
            raise ValueError("Лимит должен быть больше нуля.")
        return limit

    async def _reply_for(self, result, company_id: int, intent: dict) -> Reply:
        if result.status != "needs_approval":
            text = hide_meta_ids(result.message_ru + ("\n" + "\n".join(result.details) if result.details else ""))
            return Reply(DONE if result.status in ("done", "partial") else ERROR, text)
        approval = await io(self._executor.create_approval, result.needs_approval, company_id=company_id, intent=intent)
        text = f"{result.message_ru}\n\nПодтвердите, чтобы продолжить."
        return Reply(NEEDS_APPROVAL, text, confirm_buttons(CALLBACK, approval.id), approval.id)

    # ------------------------------------------------------------------ режим
    async def set_mode(self, company_id: int, mode: str, *, actor: str = ACTOR_USER) -> Reply:
        company = await io(self._db.companies.get, company_id)
        if company is None or mode not in MODE_TITLES:
            return Reply(ERROR, "Не получилось: компания или режим не найдены.")
        if mode == company.mode:
            return Reply(DONE, f"Режим уже «{MODE_TITLES[mode]}».")
        if mode == "confirm":
            return await self._store_mode(company, mode, actor)
        intent = {"op": "mode", "company_id": company_id, "mode": mode, "actor": actor}
        approval = await io(self._db.approvals.create, kind=APPROVAL_MODE, now=self._clock.now(),
                            company_id=company_id, payload={"intent": intent})
        text = AUTONOMOUS_WARNING.format(name=company.name)
        return Reply(NEEDS_APPROVAL, text, confirm_buttons(CALLBACK, approval.id, "✅ Включить автономный"),
                     approval.id)

    async def _store_mode(self, company: Company, mode: str, actor: str) -> Reply:
        await io(self._db.companies.update, replace(company, mode=mode))
        await self._journal.write(actor, "mode_change", company.id, "ok",
                                  summary=f"{MODE_TITLES[company.mode]} → {MODE_TITLES[mode]}")
        return Reply(DONE, f"Режим компании «{company.name}»: {MODE_TITLES[mode]}.")

    # ------------------------------------------------------------------ подтверждение
    async def confirm(self, approval_id: str) -> Reply:
        intent = await io(intent_of, self._db, approval_id)
        if intent is None:
            return Reply(ERROR, "Это подтверждение уже использовано или отменено. Повторите действие заново.")
        op, cid, actor = intent["op"], intent["company_id"], intent.get("actor", ACTOR_USER)
        if op == "resume":
            return await self.resume(cid, actor=actor, approval_id=approval_id)
        if op in ("limit_amount", "limit"):
            limit = Money(int(intent["amount_minor"]), intent["currency"])
        if op == "limit_amount":  # сумма подтверждена; рост расхода потребует ещё и разрешения Исполнителя
            if await io(self._db.approvals.consume, approval_id, self._clock.now()) is None:
                return Reply(ERROR, "Подтверждение просрочено. Задайте лимит заново.")
            return await self._apply_limit(cid, limit, actor, None)
        if op == "limit":
            return await self._apply_limit(cid, limit, actor, approval_id)
        return await self._confirm_mode(approval_id, intent, actor)

    async def _confirm_mode(self, approval_id: str, intent: dict, actor: str) -> Reply:
        if await io(self._db.approvals.consume, approval_id, self._clock.now()) is None:
            return Reply(ERROR, "Подтверждение просрочено. Запросите смену режима заново.")
        company = await io(self._db.companies.get, intent["company_id"])
        if company is None:
            return Reply(ERROR, "Такой компании нет.")
        return await self._store_mode(company, intent["mode"], actor)

    async def cancel(self, approval_id: str) -> Reply:
        await io(self._db.approvals.cancel, approval_id)
        return Reply(DONE, "Хорошо, ничего не меняю.")

    # ------------------------------------------------------------------ чтение из базы
    def _load_rows(self) -> tuple[str, list[CompanyRow]]:
        level = str(self._db.settings.get(SETTING_LEVEL, DEFAULT_LEVEL))
        return level, [self._row(c) for c in self._db.companies.list()]

    def _load_card(self, company_id: int):
        company = self._db.companies.get(company_id)
        if company is None:
            return None
        return self._row(company), company, str(self._db.settings.get(SETTING_LEVEL, DEFAULT_LEVEL))

    def _row(self, company: Company) -> CompanyRow:
        campaigns = self._db.objects.list(company_id=company.id, kind="campaign")
        live = [c for c in campaigns if c.status not in ("DELETED", "ARCHIVED", "RETIRED")]
        spent = self._spent_month(company)
        if self._db.settings.get(SETTING_HALTED, DEFAULT_HALTED):
            status = "Стоп-кран"
        elif not live:
            status = "Нет рекламы"
        elif company.monthly_limit and spent and spent.amount_minor >= company.monthly_limit.amount_minor:
            status = LIMIT_STATUS
        else:
            status = "Работает" if any(c.status == "ACTIVE" for c in live) else "На паузе"
        return CompanyRow(company.id, company.name, status, company.mode, spent,
                          company.monthly_limit, bool(company.profile.get("demo")))

    def _spent_month(self, company: Company) -> Money | None:
        if not company.currency:
            return None
        tz = company.timezone or self._db.settings.get(SETTING_REPORT_TZ, self._settings.report_tz)
        today: date = to_account_time(self._clock.now(), tz).date()
        adsets = {o.meta_id for o in self._db.objects.list(company_id=company.id, kind="adset")}
        rows = self._db.stats.list_range(company.id, today.replace(day=1).isoformat(), today.isoformat())
        total = sum(r.spend.amount_minor for r in rows if r.meta_id in adsets and r.spend)
        return Money(total, company.currency)


def _money_json(money: Money) -> dict:
    return {"amount_minor": money.amount_minor, "currency": money.currency}
