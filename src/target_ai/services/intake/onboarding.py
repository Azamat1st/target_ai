"""Анкета новой компании (R09–R15): свободный текст, короткие вопросы по одному, выбор активов кнопками.

Состояние хранится в `dialog_state` (переживает перезапуск). Компания и `monthly_limit` записываются
только кнопкой «✅ Всё верно» (потребление подтверждения атомарно).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from decimal import Decimal
from typing import Any

from target_ai.agents import Objective, parse_intent
from target_ai.llm.errors import LLMError
from target_ai.meta.errors import MetaError
from target_ai.money import Money, MoneyError, format_ru, from_major, parse_amount_ru
from target_ai.notify import Buttons
from target_ai.storage import Company, DialogState

from .extract import HANDLE_RE, MAX_AMOUNT_FRAGMENT, OBJECTIVE_TITLES, extract_fields, find_amount_fragment, parse_objective
from .geo import find_place
from .texts import button
from target_ai.config.runtime import DEFAULT_LEVEL, SETTING_LEVEL

log = logging.getLogger(__name__)

STATE = "onb"
KIND_PICK, KIND_CONFIRM, KIND_CHECK = "onb_pick", "onb_confirm", "onb_check"
CHECK_LABEL = "🔌 Проверить подключение"
LINK_HINT = "Как привязать: настройки страницы Facebook -> «Связанные аккаунты» -> Instagram."
OnCreated = Callable[[int, int], Awaitable[None]]


class Onboarding:
    def __init__(self, db, gateway_factory, llm, clock, notifier, *, on_created: OnCreated | None = None) -> None:
        self._db, self._factory, self._llm = db, gateway_factory, llm
        self._clock, self._notifier, self._on_created = clock, notifier, on_created

    # ------------------------------------------------------------------ вход
    async def start(self, chat_id: int, text: str | None = None) -> None:
        """«➕ Компания» (text=None) или «добавь кофейню @x, Берлин, …» — один путь к одной анкете."""
        data: dict[str, Any] = {}
        if text:
            data = await self._prefill(text)
        await self._save(chat_id, data)
        await self._advance(chat_id, data)

    async def handle_text(self, chat_id: int, text: str) -> bool:
        dialog = await self._dialog(chat_id)
        if dialog is None:
            return False
        step = dialog.data.get("step", "")
        handler = {
            "handle": self._answer_handle, "geo": self._answer_geo, "objective": self._answer_objective,
            "amount": self._answer_amount, "amount_account": self._answer_amount_account,
        }.get(step)
        if handler is None:
            await self._say(chat_id, "Выберите вариант кнопкой под вопросом выше или нажмите «✖️ Отмена».")
            return True
        await handler(chat_id, dialog.data, text.strip())
        return True

    async def handle_callback(self, chat_id: int, approval_id: str, choice: str) -> bool:
        approval = await self._io(self._db.approvals.get, approval_id)
        if approval is None or not approval.kind.startswith("onb_") or approval.kind == KIND_CHECK:
            return False
        used = await self._io(self._db.approvals.consume, approval_id, self._clock.now())
        if used is None:
            await self._say(chat_id, "Эта кнопка уже использована или устарела.")
            return True
        dialog = await self._dialog(chat_id)
        if dialog is None:
            await self._say(chat_id, "Анкета уже закрыта. Нажмите «➕ Компания», чтобы начать заново.")
            return True
        if approval.kind == KIND_CONFIRM:
            await self._confirm(chat_id, dialog.data)
        elif choice == "cancel":
            await self._io(self._db.dialogs.delete, chat_id)
            await self._say(chat_id, "Анкета отменена. Ничего не записано.")
        elif approval.kind == KIND_PICK:
            await self._picked(chat_id, dialog.data, approval.payload)
        return True

    # ------------------------------------------------------------------ предзаполнение
    async def _prefill(self, text: str) -> dict[str, Any]:
        found = extract_fields(text)
        data: dict[str, Any] = {}
        intent = None
        try:
            intent = await parse_intent(self._llm, text)
        except LLMError as exc:
            log.info("Разбор намерения недоступен: %s", exc)
        handle = found.handle or (intent.instagram_handle if intent else None)
        if handle and HANDLE_RE.match(handle.strip()):
            data["handle"] = HANDLE_RE.match(handle.strip()).group(1)
        place = found.place or (find_place(intent.geo) if intent and intent.geo else None)
        if place:
            data.update(self._place_data(place))
        if found.cpa_text:
            cpa: dict[str, Any] = {}
            if self._set_amount(cpa, found.cpa_text) is None:
                data["cpa"] = {"amount": cpa["amount"], "currency": cpa["amount_currency"]}
        objective = found.objective or (intent.objective if intent else None)
        if objective:
            data["objective"] = Objective(objective).value
        site = found.website or (intent.website if intent else None)
        if site:
            data["website"] = site
        if intent and intent.company and intent.company.strip() != data.get("handle"):
            data["name"] = intent.company.strip()[:80]
        fragment = found.amount_text or (intent.amount_text if intent else None)
        if fragment:
            self._set_amount(data, fragment, quiet=True)
        return data

    @staticmethod
    def _place_data(place) -> dict[str, Any]:
        return {"place_name": place.name, "countries": list(place.countries), "geo_line": place.summary}

    @staticmethod
    def _set_amount(data: dict[str, Any], fragment: str, *, quiet: bool = False) -> str | None:
        try:
            value, currency = parse_amount_ru(fragment)
        except ValueError as exc:
            return str(exc) or "Не понял сумму."
        if value <= 0:
            return "Бюджет должен быть больше нуля."
        data["amount"], data["amount_currency"] = str(value), currency
        return None

    # ------------------------------------------------------------------ ответы на вопросы
    async def _answer_handle(self, chat_id: int, data: dict, text: str) -> None:
        match = HANDLE_RE.match(text)
        if not match:
            await self._say(chat_id, "Ник — без пробелов: латиница, цифры, точка и «_». Пример: @coffee_x. Напишите ещё раз.")
            return
        await self._next(chat_id, {**data, "handle": match.group(1)})

    async def _answer_geo(self, chat_id: int, data: dict, text: str) -> None:
        place = find_place(text)
        if place is None:
            await self._say(chat_id, "Не знаю такого места. Напишите страну показа (например, Германия) — "
                                     "показ в любом случае идёт по стране.")
            return
        await self._next(chat_id, {**data, **self._place_data(place)})

    async def _answer_objective(self, chat_id: int, data: dict, text: str) -> None:
        objective = parse_objective(text)
        if objective is None:
            await self._say(chat_id, "Не понял цель. Выберите: сообщения, заявки, трафик на сайт, трафик в профиль или охват.")
            return
        await self._next(chat_id, {**data, "objective": objective.value})

    async def _answer_amount(self, chat_id: int, data: dict, text: str) -> None:
        fragment = text if len(text) <= MAX_AMOUNT_FRAGMENT else (find_amount_fragment(text) or text)
        fresh = dict(data)
        problem = self._set_amount(fresh, fragment)
        if problem:
            await self._say(chat_id, f"{problem}\nНапишите месячный бюджет числом, например: 1500 евро.")
            return
        await self._next(chat_id, fresh)

    async def _answer_amount_account(self, chat_id: int, data: dict, text: str) -> None:
        fresh = dict(data)
        problem = self._set_amount(fresh, text)
        account_currency = data["account"]["currency"]
        if problem is None and fresh["amount_currency"] not in (None, account_currency):
            problem = f"Нужна сумма в валюте аккаунта — {account_currency}."
        if problem:
            await self._say(chat_id, f"{problem}\nКурс я не пересчитываю. Напишите сумму в {account_currency}.")
            return
        await self._next(chat_id, {**fresh, "amount_currency": None})

    # ------------------------------------------------------------------ продвижение по шагам
    async def _next(self, chat_id: int, data: dict) -> None:
        await self._save(chat_id, data)
        await self._advance(chat_id, data)

    async def _advance(self, chat_id: int, data: dict) -> None:
        if not data.get("handle"):
            return await self._ask(chat_id, data, "handle", "Какой Instagram-ник у компании? Например: @coffee_x")
        if not data.get("countries"):
            return await self._ask(chat_id, data, "geo", "В каком городе или стране показывать рекламу?")
        if not data.get("objective"):
            return await self._ask_objective(chat_id, data)
        if not data.get("amount"):
            return await self._ask(chat_id, data, "amount", "Какой месячный бюджет? Например: 1500 евро")
        if not data.get("account"):
            return await self._choose_account(chat_id, data)
        if not data.get("page"):
            return await self._choose_page(chat_id, data)
        account_currency = data["account"]["currency"]
        if data.get("amount_currency") not in (None, account_currency):
            return await self._ask(chat_id, data, "amount_account",
                f"Вы указали бюджет в {data['amount_currency']}, а валюта вашего рекламного аккаунта — "
                f"{account_currency}. Курс я не пересчитываю. Напишите месячный бюджет в {account_currency}.")
        await self._summary(chat_id, data)

    async def _ask(self, chat_id: int, data: dict, step: str, question: str) -> None:
        await self._save(chat_id, {**data, "step": step})
        await self._say(chat_id, question, [[self._cancel_button(chat_id)]])

    async def _ask_objective(self, chat_id: int, data: dict) -> None:
        await self._save(chat_id, {**data, "step": "objective"})
        rows = [[self._pick(OBJECTIVE_TITLES[o], chat_id, field="objective", value=o.value)] for o in Objective]
        await self._say(chat_id, "Что нужно от рекламы? Выберите кнопкой или напишите словами.", [*rows, [self._cancel_button(chat_id)]])

    async def _choose_account(self, chat_id: int, data: dict) -> None:
        await self._save(chat_id, {**data, "step": "account"})
        try:
            accounts = await self._factory(False).list_ad_accounts()
        except MetaError as exc:
            return await self._no_assets(chat_id, f"Не удалось получить рекламные аккаунты: {exc.user_message_ru}")
        active = [a for a in accounts if a.is_active]
        if not active:
            reason = "все рекламные аккаунты ограничены или закрыты" if accounts else "токену Meta не доступен ни один рекламный аккаунт"
            return await self._no_assets(chat_id, f"Не могу продолжить: {reason}.")
        if len(active) == 1:
            return await self._set_account(chat_id, data, self._account_info(active[0]))
        rows = [[self._pick(f"{a.name} ({a.currency})", chat_id, field="account", value=self._account_info(a))] for a in active]
        await self._say(chat_id, "Выберите рекламный аккаунт:", [*rows, [self._cancel_button(chat_id)]])

    async def _choose_page(self, chat_id: int, data: dict) -> None:
        await self._save(chat_id, {**data, "step": "page"})
        try:
            pages = await self._factory(False).list_pages()
        except MetaError as exc:
            return await self._no_assets(chat_id, f"Не удалось получить страницы: {exc.user_message_ru}")
        if not pages:
            return await self._no_assets(chat_id, "Не нашёл ни одной страницы Facebook, доступной токену. Реклама в Instagram идёт от имени страницы.")
        if len(pages) == 1:
            return await self._set_page(chat_id, data, {"id": str(pages[0]["id"]), "name": str(pages[0].get("name", ""))})
        rows = [[self._pick(str(p.get("name") or p["id"]), chat_id, field="page", value={"id": str(p["id"]), "name": str(p.get("name", ""))})] for p in pages]
        await self._say(chat_id, "Выберите страницу, от имени которой пойдёт реклама:", [*rows, [self._cancel_button(chat_id)]])

    async def _no_assets(self, chat_id: int, reason: str) -> None:
        button_row = [button(self._db, self._clock.now(), CHECK_LABEL, KIND_CHECK, "check", None, {"chat_id": chat_id})]
        await self._say(chat_id, f"{reason}\nПроверьте подключение — я подскажу, чего не хватает.", [button_row])

    async def _picked(self, chat_id: int, data: dict, payload: dict) -> None:
        field, value = payload.get("field"), payload.get("value")
        if payload.get("chat_id") != chat_id:
            return await self._say(chat_id, "Эта кнопка из другой анкеты.")
        if field == "objective":
            return await self._next(chat_id, {**data, "objective": value})
        if field == "account":
            return await self._set_account(chat_id, data, value)
        if field == "page":
            return await self._set_page(chat_id, data, value)

    async def _set_account(self, chat_id: int, data: dict, account: dict) -> None:
        await self._next(chat_id, {**data, "account": account})

    async def _set_page(self, chat_id: int, data: dict, page: dict) -> None:
        try:
            ig_id = await self._factory(False).get_page_instagram(page["id"])
        except MetaError as exc:
            return await self._no_assets(chat_id, f"Не удалось проверить Instagram страницы: {exc.user_message_ru}")
        if not ig_id:
            await self._save(chat_id, {**data, "step": "page"})
            return await self._no_assets(chat_id, f"К странице «{page['name']}» не привязан Instagram business-аккаунт. {LINK_HINT}")
        await self._next(chat_id, {**data, "page": page, "ig_user_id": ig_id})

    # ------------------------------------------------------------------ сводка и запись
    async def _summary(self, chat_id: int, data: dict) -> None:
        currency = data["account"]["currency"]
        try:
            limit = from_major(Decimal(data["amount"]), currency)
        except MoneyError as exc:
            return await self._ask(chat_id, data, "amount_account", f"{exc} Напишите месячный бюджет в {currency}.")
        cpa, cpa_line = self._cpa_money(data, currency)
        data = {**data, "step": "confirm", "limit": {"amount_minor": limit.amount_minor, "currency": currency},
                "cpa_money": ({"amount_minor": cpa.amount_minor, "currency": currency} if cpa else None)}
        await self._save(chat_id, data)
        lines = [
            "Проверьте данные компании:",
            f"• Название: {data.get('name') or '@' + data['handle']}",
            f"• Instagram: @{data['handle']}",
            f"• Сайт: {data.get('website') or 'нет'}",
            f"• Гео: {data['place_name']} ({', '.join(data['countries'])}) — {data['geo_line']}",
            f"• Цель: {OBJECTIVE_TITLES[Objective(data['objective'])]}",
            f"• Рекламный аккаунт: {data['account']['name']} ({currency})",
            f"• Страница: {data['page']['name']}",
            *([cpa_line] if cpa_line else []),
            "",
            f"Месячный лимит: {format_ru(limit)} — валюта вашего рекламного аккаунта. "
            "Лимит запишется только после «✅ Всё верно».",
        ]
        now = self._clock.now()
        rows = [[button(self._db, now, "✅ Всё верно", KIND_CONFIRM, "ok", None, {"chat_id": chat_id}),
                 self._cancel_button(chat_id)]]
        await self._say(chat_id, "\n".join(lines), rows)

    @staticmethod
    def _cpa_money(data: dict, currency: str) -> tuple[Money | None, str | None]:
        raw = data.get("cpa")
        if not raw:
            return None, None
        if raw["currency"] not in (None, currency):
            return None, "• Желаемая цена результата указана в другой валюте — пропустил, уточню при составлении плана."
        try:
            money = from_major(Decimal(raw["amount"]), currency)
        except MoneyError:
            return None, "• Желаемую цену результата не разобрал — уточню при составлении плана."
        return money, f"• Желаемая цена результата: {format_ru(money)}"

    async def _confirm(self, chat_id: int, data: dict) -> None:
        if data.get("step") != "confirm" or "limit" not in data:
            return await self._say(chat_id, "Анкета не готова к записи. Продолжим по вопросам.")
        limit = Money(int(data["limit"]["amount_minor"]), data["limit"]["currency"])
        level = await self._io(self._db.settings.get, SETTING_LEVEL, DEFAULT_LEVEL)
        company = Company(
            name=data.get("name") or f"@{data['handle']}", instagram_handle=data["handle"], website=data.get("website"),
            ad_account_id=data["account"]["id"], page_id=data["page"]["id"], ig_user_id=data["ig_user_id"],
            currency=limit.currency, timezone=data["account"]["tz"], monthly_limit=limit, status="draft",
            target_cpa=(Money(int(data["cpa_money"]["amount_minor"]), data["cpa_money"]["currency"]) if data.get("cpa_money") else None),
            profile={"geo": data["place_name"], "countries": data["countries"], "objective": data["objective"],
                     "demo": level == "demo"},
            created_at=self._clock.now(),
        )
        saved = await self._io(self._db.companies.create, company)
        await self._io(self._db.dialogs.delete, chat_id)
        await self._say(chat_id, f"✅ Компания «{saved.name}» добавлена. Месячный лимит: {format_ru(limit)}.")
        if self._on_created:
            await self._on_created(saved.id, chat_id)

    # ------------------------------------------------------------------ мелочи
    @staticmethod
    def _account_info(account) -> dict:
        return {"id": account.id, "name": account.name, "currency": account.currency, "tz": account.timezone_name}

    def _pick(self, label: str, chat_id: int, **payload: Any) -> tuple[str, str]:
        return button(self._db, self._clock.now(), label, KIND_PICK, "pick", None, {"chat_id": chat_id, **payload})

    def _cancel_button(self, chat_id: int) -> tuple[str, str]:
        return button(self._db, self._clock.now(), "✖️ Отмена", KIND_PICK, "cancel", None, {"chat_id": chat_id})

    async def _save(self, chat_id: int, data: dict) -> None:
        await self._io(self._db.dialogs.save, DialogState(chat_id=chat_id, state=STATE, data=data, updated_at=self._clock.now()))

    async def _dialog(self, chat_id: int) -> DialogState | None:
        dialog = await self._io(self._db.dialogs.get, chat_id)
        return dialog if dialog is not None and dialog.state == STATE else None

    async def _say(self, chat_id: int, text: str, buttons: Buttons | None = None) -> None:
        await self._notifier.send(chat_id, text, buttons)

    @staticmethod
    async def _io(fn, *args):
        import asyncio
        return await asyncio.to_thread(fn, *args)
