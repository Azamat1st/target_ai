"""Стоп-кран и «Возобновить систему» (истории 69–70): тонкая обёртка над Исполнителем с кнопками."""

from __future__ import annotations

from target_ai.clock import Clock
from target_ai.executor import ExecutorError
from target_ai.storage import Database

from .common import ACTOR_USER, DONE, ERROR, NEEDS_APPROVAL, Reply, confirm_buttons, io
from target_ai.config.runtime import DEFAULT_HALTED, SETTING_HALTED

CALLBACK = "sys"  # sys:ok:<подтверждение> · sys:no:<подтверждение>
APPROVAL_KIND = "resume_system"


class SystemControl:
    def __init__(self, db: Database, clock: Clock, executor) -> None:
        self._db, self._clock, self._executor = db, clock, executor

    async def stop(self, *, actor: str = ACTOR_USER) -> Reply:
        report = await self._executor.halt_all(actor=actor)
        return Reply(DONE, report.message_ru)

    async def request_resume(self) -> Reply:
        if not await io(self._db.settings.get, SETTING_HALTED, DEFAULT_HALTED):
            return Reply(DONE, "Стоп-кран не включён: возобновлять нечего.")
        approval = await io(self._db.approvals.create, kind=APPROVAL_KIND, now=self._clock.now())
        text = ("Снять стоп-кран?\n\nЧто изменится: система снова сможет включать и увеличивать расход. "
                "Само по себе ничего не включится — каждую компанию вы возобновляете отдельной кнопкой.")
        return Reply(NEEDS_APPROVAL, text, confirm_buttons(CALLBACK, approval.id, "✅ Возобновить систему"),
                     approval.id)

    async def confirm_resume(self, approval_id: str, *, actor: str = ACTOR_USER) -> Reply:
        try:
            result = await self._executor.resume_system(approval_id, actor=actor)
        except ExecutorError as exc:
            return Reply(ERROR, exc.message_ru)
        return Reply(DONE, result.message_ru)

    async def cancel(self, approval_id: str) -> Reply:
        await io(self._db.approvals.cancel, approval_id)
        return Reply(DONE, "Хорошо, стоп-кран остаётся включённым.")
