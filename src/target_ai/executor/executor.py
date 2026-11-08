"""Исполнитель: единственный код, который что-либо меняет в Meta (спека §4)."""

from __future__ import annotations

from target_ai.policy import NeedsApproval

from .controls import Controls
from .engagement import Engagement
from target_ai.meta.errors import MetaError

from .probe import standin_for


class Executor(Controls, Engagement):
    """`Executor(db, gateway_factory, policy, clock, notifier)`.

    `gateway_factory(validate_only: bool) -> MetaGateway` возвращает переиспользуемый клиент
    (на тестовом уровне вызывается и с `validate_only=True`). `policy` — модуль/объект с
    `check_action`, `action_key`, `check_decisions`, `enforce_pace` (None — `target_ai.policy`).
    `notifier.send(chat_id | None, text, buttons)`; кнопки — строки пар (подпись, callback_data).
    """

    async def build_snapshot(self, company_id: int):
        """Снимок компании для `decide`/`check_action` у вызывающих сервисов."""
        return await self.snapshot(company_id)

    def create_approval(self, needs: NeedsApproval, *, company_id: int | None, ttl=None):
        """Создаёт подтверждение под решение политики (ключ уходит в payload)."""
        kwargs = {"ttl": ttl} if ttl is not None else {}
        return self._db.approvals.create(kind=needs.kind, now=self._clock.now(), company_id=company_id,
                                         payload={"key": needs.key, "summary": needs.summary_ru}, **kwargs)

    async def probe_ig_access(self, account_id: str, page_id: str, ig_user_id: str, *,
                              website: str | None = None) -> None:
        """Проверочное создание креатива (`validate_only`): есть ли у аккаунта доступ к Instagram.

        Креатив ссылается на настоящий пост IG (нет постов - ссылка без картинки). Ничего не создаёт и
        не пишет в реестр; отказ Meta - `MetaError` (подсубкод 1815199 - `kind="ig_access"`).
        Одна строка журнала пишется ПОСЛЕ ответа Meta: `ok` или `error` с кодом.
        """
        action, info = "проверка доступа к Instagram", {"account_id": account_id, "page_id": page_id,
                                                         "ig_user_id": ig_user_id}
        try:
            params = await standin_for(self.gateway(), name="[ПРОВЕРКА] доступ к Instagram", page_id=page_id,
                                       ig_user_id=ig_user_id, website=website)
            await self.gateway(validate_only=True).create_creative(account_id, params)
        except MetaError as exc:
            await self.audit("system", action, None, "error", message=exc.user_message_ru, meta_code=exc.code,
                             meta_subcode=exc.subcode, meta_kind=exc.kind, fbtrace_id=exc.fbtrace_id, **info)
            raise
        await self.audit("system", action, None, "ok", **info)
