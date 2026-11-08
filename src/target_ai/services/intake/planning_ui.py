"""Диалоги и кнопки медиаплана: «Запустить / Изменить / Отмена», правка словами, вопросы перед планом."""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from typing import Any

from target_ai.agents import Objective
from target_ai.money import Money, MoneyError, format_ru, from_major, parse_amount_ru
from target_ai.executor import ApprovalInvalid, ExecutorError, PolicyDenied
from target_ai.storage import DialogState, Plan

from .geo import EU_COUNTRIES
from .texts import button

STATE_EDIT, STATE_PRIVACY, STATE_CPA = "plan:edit", "plan:privacy", "plan:cpa"
STATE_DSA_BENEFICIARY, STATE_DSA_PAYOR = "plan:dsa_beneficiary", "plan:dsa_payor"
KIND_START, KIND_EDIT, KIND_CANCEL, KIND_GO = "plan_start", "plan_edit", "plan_cancel", "plan_go"
KIND_CPA = "plan_cpa"
KIND_QCANCEL = "plan_qcancel"
QUESTION_STATES = (STATE_PRIVACY, STATE_CPA, STATE_DSA_BENEFICIARY, STATE_DSA_PAYOR)
RESULT_NOUN = {
    Objective.DIRECT_MESSAGES: "сообщения", Objective.LEAD_FORM: "заявки", Objective.WEBSITE_TRAFFIC: "перехода на сайт",
    Objective.PROFILE_TRAFFIC: "визита в профиль", Objective.REACH: "1 000 охвата",
}
STALE_TEXT = "План устарел: есть более новая версия или кнопка уже использована."
_URL_RE = re.compile(r"^https?://[^\s/]+\.[^\s/]+\S*$", re.IGNORECASE)
MAX_FIELD = 200


class PlanInteractions:
    """Миксин `Planning`: нужны `_db, _clock, _notifier, _executor` и метод `propose`."""

    _db: Any
    _clock: Any
    _notifier: Any
    _executor: Any

    async def handle_text(self, chat_id: int, text: str) -> bool:
        dialog = await self._io(self._db.dialogs.get, chat_id)
        if dialog is None or not dialog.state.startswith("plan:"):
            return False
        data, body = dialog.data, text.strip()
        if dialog.state == STATE_EDIT:
            await self._io(self._db.dialogs.delete, chat_id)
            await self.propose(int(data["company_id"]), chat_id, edit_request=body[:900])
        elif dialog.state == STATE_PRIVACY:
            await self._answer_privacy(chat_id, data, body)
        elif dialog.state == STATE_DSA_BENEFICIARY:
            await self._ask(chat_id, STATE_DSA_PAYOR, {**data, "beneficiary": body[:MAX_FIELD]},
                            "Кто плательщик рекламы (кто платит за неё)? Напишите название или имя.")
        elif dialog.state == STATE_CPA:
            await self._answer_cpa(chat_id, data, body)
        elif dialog.state == STATE_DSA_PAYOR:
            await self._answer_payor(chat_id, data, body)
        return True

    async def handle_callback(self, chat_id: int, approval_id: str, choice: str) -> bool:
        approval = await self._io(self._db.approvals.get, approval_id)
        if approval is None:
            return False
        if approval.kind == KIND_QCANCEL:
            if await self._consume(chat_id, approval_id):
                await self._io(self._db.dialogs.delete, chat_id)
                await self._notifier.send(chat_id, "Вопрос снят. Ничего не записано и не запущено.", [])
            return True
        if approval.kind == KIND_START:
            if await self._consume(chat_id, approval_id):
                await self.propose(int(approval.payload["company_id"]), chat_id)
            return True
        plan = await self._plan_of(approval)
        if plan is None and approval.kind not in (KIND_EDIT, KIND_CANCEL, KIND_GO, KIND_CPA):
            return False
        if plan is None or plan.status not in ("proposed", "draft") or approval.status != "pending":
            await self._notifier.send(chat_id, STALE_TEXT, [])
            return True
        if approval.kind == KIND_EDIT:
            await self._start_edit(chat_id, approval_id, plan)
        elif approval.kind == KIND_CANCEL:
            await self._cancel(chat_id, approval_id, plan)
        elif approval.kind == KIND_CPA:
            await self._cpa_confirmed(chat_id, approval, plan)
        elif choice == "go" and plan.status == "proposed":
            await self._go(chat_id, approval, plan)
        else:
            await self._notifier.send(chat_id, STALE_TEXT, [])
        return True

    # ------------------------------------------------------------------ кнопки
    async def _start_edit(self, chat_id: int, approval_id: str, plan: Plan) -> None:
        if not await self._consume(chat_id, approval_id):
            return
        await self._ask(chat_id, STATE_EDIT, {"company_id": plan.company_id, "plan_id": plan.id},
                        "Напишите словами, что изменить: например «убери ретаргетинг, бюджет на интересы больше, текст короче».")

    async def _cancel(self, chat_id: int, approval_id: str, plan: Plan) -> None:
        if not await self._consume(chat_id, approval_id):
            return
        await self._io(self._cancel_plan, plan)
        await self._notifier.send(chat_id, f"Медиаплан v{plan.version} отменён. В Meta ничего не создано.", [])

    def _cancel_plan(self, plan: Plan) -> None:
        with self._db.transaction():
            for other in self._db.approvals.list(company_id=plan.company_id):
                if other.id in plan.data.get("approvals", {}).values():
                    self._db.approvals.cancel(other.id)
            self._db.plans.update(replace(plan, status="cancelled", updated_at=self._clock.now()))

    async def _go(self, chat_id: int, approval, plan: Plan) -> None:
        approval_id = None if approval.kind == KIND_GO else approval.id
        try:
            result = await self._executor.launch(plan.id, approval_id)
        except (ApprovalInvalid, PolicyDenied, ExecutorError) as exc:
            company = await self._io(self._db.companies.get, plan.company_id)
            if isinstance(exc, PolicyDenied) and await self._ask_missing(chat_id, company, exc.code, plan.data.get("edit_request")):
                return
            text = STALE_TEXT if isinstance(exc, ApprovalInvalid) else str(getattr(exc, "reason_ru", None) or exc)
            await self._notifier.send(chat_id, text, [])
            return
        await self._notifier.send(chat_id, result.message_ru, [])
        if result.status not in ("needs_approval", "busy"):
            await self._accept_target(plan)  # цена — договорённость о принятом плане, а не объект Meta

    async def _accept_target(self, plan: Plan) -> None:
        """«Запустить» принимает целевую цену плана: она становится Company.target_cpa (не действие в Meta)."""
        raw = plan.data.get("target_cpa")

        def apply() -> None:
            company = self._db.companies.get(plan.company_id)
            if raw and company.target_cpa is None:
                self._db.companies.update(replace(company, target_cpa=Money(int(raw["amount_minor"]), raw["currency"])))
        await self._io(apply)

    async def _answer_cpa(self, chat_id: int, data: dict, text: str) -> None:
        company = await self._io(self._db.companies.get, int(data["company_id"]))
        try:
            value, currency = parse_amount_ru(text)
            if value <= 0 or currency not in (None, company.currency):
                raise ValueError(f"Нужна сумма больше нуля в валюте аккаунта — {company.currency}.")
            money = from_major(value, company.currency)
        except ValueError as exc:  # MoneyError тоже ValueError
            await self._notifier.send(chat_id, f"{exc or 'Не понял сумму.'}\nНапишите цену числом, например: 2 {company.currency}.", [])
            return
        ok = button(self._db, self._clock.now(), "✅ Всё верно", KIND_CPA, "ok", company.id,
                    {"plan_id": int(data["plan_id"]), "amount_minor": money.amount_minor, "currency": money.currency})
        await self._notifier.send(chat_id, f"Целевая цена результата: {format_ru(money)} — ваша. Верно?", [[ok]])

    async def _cpa_confirmed(self, chat_id: int, approval, plan: Plan) -> None:
        if not await self._consume(chat_id, approval.id):
            return
        p = approval.payload
        target = {"amount_minor": p["amount_minor"], "currency": p["currency"]}
        await self._io(self._db.dialogs.delete, chat_id)
        fresh = await self._io(self._save_target, plan, target)
        company = await self._io(self._db.companies.get, plan.company_id)
        ids = fresh.data.get("approvals", {})
        row = [("✏️ Изменить", f"ap:{ids['edit']}:edit"), ("✖️ Отмена", f"ap:{ids['cancel']}:cancel")]
        await self._offer_launch(chat_id, company, fresh, f"Цена принята: {format_ru(Money(**{'amount_minor': target['amount_minor'], 'currency': target['currency']}))}.", row, dict(ids))

    def _save_target(self, plan: Plan, target: dict) -> Plan:
        current = self._db.plans.get(plan.id)
        return self._db.plans.update(replace(current, data={**current.data, "target_cpa": target}, updated_at=self._clock.now()))

    async def _ask_missing(self, chat_id: int, company, code: str, edit_request) -> bool:
        """Отказ запуска из-за нехватки данных — вопрос пользователю, а не ошибка."""
        ctx = {"company_id": company.id, "edit_request": edit_request}
        if code == "privacy_policy_missing":
            await self._ask(chat_id, STATE_PRIVACY, ctx, "Для лид-формы нужна ссылка на политику конфиденциальности "
                                                         "вашей компании. Пришлите ссылку (https://…).")
            return True
        if code == "dsa_missing":
            await self._ask(chat_id, STATE_DSA_BENEFICIARY, ctx, "Реклама в странах ЕС: Meta требует указать получателя "
                            "и плательщика. Кто получатель рекламы? Напишите название или имя.")
            return True
        return False

    # ------------------------------------------------------------------ вопросы перед планом
    async def _preflight(self, company, objective, chat_id: int, edit_request: str | None) -> bool:
        """True — задан вопрос, план продолжится после ответа."""
        ctx = {"company_id": company.id, "edit_request": edit_request}
        profile = company.profile
        if objective.value == "lead_form" and not profile.get("privacy_url"):
            await self._ask(chat_id, STATE_PRIVACY, ctx, "Для лид-формы нужна ссылка на политику конфиденциальности "
                                                          "вашей компании. Пришлите ссылку (https://…).")
            return True
        if EU_COUNTRIES.intersection(profile.get("countries", ())) and not profile.get("dsa"):
            await self._ask(chat_id, STATE_DSA_BENEFICIARY, ctx, "Реклама в странах ЕС: Meta требует указать получателя "
                            "и плательщика. Кто получатель рекламы (для кого она показывается)? Напишите название или имя.")
            return True
        return False

    async def _answer_privacy(self, chat_id: int, data: dict, url: str) -> None:
        if not _URL_RE.match(url):
            await self._notifier.send(chat_id, "Это не похоже на ссылку. Пришлите адрес вида https://example.com/privacy.", [])
            return
        await self._update_profile(int(data["company_id"]), {"privacy_url": url})
        await self._io(self._db.dialogs.delete, chat_id)
        await self.propose(int(data["company_id"]), chat_id, edit_request=data.get("edit_request"), retry=True)

    async def _answer_payor(self, chat_id: int, data: dict, payor: str) -> None:
        dsa = {"beneficiary": data["beneficiary"], "payor": payor[:MAX_FIELD]}
        await self._update_profile(int(data["company_id"]), {"dsa": dsa})
        await self._io(self._db.dialogs.delete, chat_id)
        await self.propose(int(data["company_id"]), chat_id, edit_request=data.get("edit_request"), retry=True)

    async def _update_profile(self, company_id: int, changes: dict) -> None:
        def apply() -> None:
            company = self._db.companies.get(company_id)
            self._db.companies.update(replace(company, profile={**company.profile, **changes}))
        await self._io(apply)

    # ------------------------------------------------------------------ мелочи
    async def _ask(self, chat_id: int, state: str, data: dict, question: str, buttons=None) -> None:
        await self._io(self._db.dialogs.save, DialogState(chat_id=chat_id, state=state, data=data, updated_at=self._clock.now()))
        if not buttons:
            cancel = button(self._db, self._clock.now(), "✖️ Отмена", KIND_QCANCEL, "cancel", data.get("company_id"),
                            {"chat_id": chat_id})
            buttons = [[cancel]]
        await self._notifier.send(chat_id, question, buttons)

    async def _consume(self, chat_id: int, approval_id: str) -> bool:
        if await self._io(self._db.approvals.consume, approval_id, self._clock.now()) is None:
            await self._notifier.send(chat_id, STALE_TEXT, [])
            return False
        return True

    async def _plan_of(self, approval) -> Plan | None:
        def find() -> Plan | None:
            plan_id = approval.payload.get("plan_id")
            if plan_id:
                return self._db.plans.get(int(plan_id))
            for plan in self._db.plans.list(company_id=approval.company_id):
                if plan.data.get("approvals", {}).get("go") == approval.id:
                    return plan
            return None
        return await self._io(find)

    def _plan_button(self, label: str, kind: str, choice: str, company_id: int, plan_id: int) -> tuple[str, str]:
        return button(self._db, self._clock.now(), label, kind, choice, company_id, {"plan_id": plan_id})

    @staticmethod
    async def _io(fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
