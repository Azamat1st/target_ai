"""Исполнитель: единственный код, который что-либо меняет в Meta (спека §4)."""

from __future__ import annotations

from target_ai.policy import NeedsApproval

from .controls import Controls
from .validation import PLACEHOLDER_HASH


class Executor(Controls):
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

    async def probe_ig_access(self, account_id: str, page_id: str, ig_user_id: str) -> None:
        """Проверочное создание креатива (`validate_only`): есть ли у аккаунта доступ к Instagram.

        Ничего не создаёт и не пишет в реестр; отказ Meta пробрасывается как `MetaError`
        (подсубкод 1815199 - `kind="ig_access"`). В журнал уходит одна строка.
        """
        params = {"name": "[ПРОВЕРКА] доступ к Instagram",
                  "object_story_spec": {"page_id": page_id, "instagram_user_id": ig_user_id,
                                        "photo_data": {"image_hash": PLACEHOLDER_HASH, "caption": "проверка"}}}
        await self.audit("system", "проверка доступа к Instagram", None, "ok",
                         account_id=account_id, page_id=page_id, ig_user_id=ig_user_id)
        await self.gateway(validate_only=True).create_creative(account_id, params)
