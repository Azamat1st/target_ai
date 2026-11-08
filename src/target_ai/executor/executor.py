"""Исполнитель: единственный код, который что-либо меняет в Meta (спека §4)."""

from __future__ import annotations

from target_ai.policy import NeedsApproval

from .controls import Controls


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
