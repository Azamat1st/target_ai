"""Аудитория вовлечённых компании (спека §6): создаётся Исполнителем, потому что мутация без расхода."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from target_ai.meta.errors import MetaError
from target_ai.policy import CreateObject
from target_ai.storage import Company

from .errors import ExecutorError
from .results import EngagementAudience
from .saga import Saga, created_after
from .spec import RETENTION_SECONDS_YEAR

PROFILE_ID = "engagement_audience_id"
PROFILE_KEY = "engagement_audience_key"  # ключ идемпотентности незавершённой попытки
PROFILE_KEY_AT = "engagement_audience_key_at"  # когда ключ записан: старше этого времени чужое
TEST_LEVEL_NOTE = "Тестовый уровень: аудиторию не создаю, размер неизвестен."
CREATE_RETRIES = 2


class Engagement(Saga):
    async def ensure_engagement_audience(self, company_id: int) -> EngagementAudience:
        """Есть в профиле - читает размер; нет - создаёт (ключ, тег, реестр) и запоминает id в профиле."""
        company = await self.io(self._db.companies.get, company_id)
        if company is None:
            raise ExecutorError(f"Компания {company_id} не найдена.")
        async with self.company_lock(company_id):
            company = await self.io(self._db.companies.get, company_id)
            existing = company.profile.get(PROFILE_ID)
            if existing:
                return EngagementAudience(str(existing), await self.gateway().get_engagement_audience_size(existing))
            if self._level() == "test":
                return EngagementAudience("", None, TEST_LEVEL_NOTE)
            if not company.ig_user_id or not company.ad_account_id:
                raise ExecutorError("Для аудитории вовлечённых нужны рекламный аккаунт и Instagram компании.")
            audience_id = await self._create_engagement(company)
            return EngagementAudience(audience_id, await self.gateway().get_engagement_audience_size(audience_id))

    async def _create_engagement(self, company: Company) -> str:
        tag = f"[ta:{await self.io(self.install_id)}:E{company.id}:engagement]"
        pending = company.profile.get(PROFILE_KEY)
        key_at = company.profile.get(PROFILE_KEY_AT)
        if pending and (found := await self._find_audience(company, tag, key_at)):
            return await self._remember_engagement(company, found, tag)  # прошлая попытка успела создать
        await self.guard(CreateObject("audience"), company.id, actor="system")
        key = pending or str(uuid.uuid4())
        key_at = key_at or self._clock.now().isoformat()
        await self._save_profile(company, **{PROFILE_KEY: key, PROFILE_KEY_AT: key_at})  # ключ - ДО вызова
        params = self._engagement_params(company, f"Вовлечённые {tag}")
        for attempt in range(CREATE_RETRIES):
            try:
                created = await self.mutate("system", company.id, "create:engagement_audience", {"tag": tag},
                                            lambda: self.gateway().create_audience(
                                                company.ad_account_id, params, idempotency_key=key))
                return await self._remember_engagement(company, created, tag)
            except MetaError as exc:
                if not exc.outcome_unknown or attempt == CREATE_RETRIES - 1:
                    raise
                if found := await self._find_audience(company, tag, key_at):  # могло создаться: сверка по тегу
                    return await self._remember_engagement(company, found, tag)
        raise ExecutorError("Не удалось создать аудиторию вовлечённых.")

    async def _find_audience(self, company: Company, tag: str, key_at: str | None) -> str | None:
        """Аудитория с нашим тегом своя, только если создана не раньше записи о попытке; иначе — не трогаем."""
        rows = await self.gateway().find_by_name_tag(company.ad_account_id, "customaudiences", tag)
        if not rows:
            return None
        ours = next((r for r in rows if created_after(key_at, r.get("time_created"))), None)
        if ours is None:
            raise ExecutorError(f"В аккаунте есть аудитория с таким же тегом ({rows[0]['id']}) — создана не системой. "
                                "Я её не трогаю.")
        return str(ours["id"])

    async def _remember_engagement(self, company: Company, audience_id: str, tag: str) -> str:
        await self.io(self.register, company.id, None, audience_id, "audience", None, f"Вовлечённые {tag}", None,
                      "ACTIVE")
        fresh = await self.io(self._db.companies.get, company.id)
        profile = {k: v for k, v in fresh.profile.items() if k not in (PROFILE_KEY, PROFILE_KEY_AT)}
        await self.io(self._db.companies.update, replace(fresh, profile={**profile, PROFILE_ID: audience_id}))
        return audience_id

    async def _save_profile(self, company: Company, **changes: Any) -> None:
        fresh = await self.io(self._db.companies.get, company.id)
        await self.io(self._db.companies.update, replace(fresh, profile={**fresh.profile, **changes}))

    @staticmethod
    def _engagement_params(company: Company, name: str) -> dict[str, Any]:
        rule = {"inclusions": {"operator": "or", "rules": [{
            "event_sources": [{"id": company.ig_user_id, "type": "ig_business"}],
            "retention_seconds": RETENTION_SECONDS_YEAR,
            "filter": {"operator": "and", "filters": [
                {"field": "event", "operator": "eq", "value": "ig_business_profile_all"}]}}]}}
        return {"name": name, "subtype": "ENGAGEMENT", "prefill": 1, "rule": rule}
