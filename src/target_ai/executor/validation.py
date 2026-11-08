"""Тестовый уровень: проверочные запросы `validate_only`, временные родители «[ТЕСТ]» (спека §4, §6)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from typing import Any

from target_ai.meta.errors import MetaError
from target_ai.policy import CreateObject, Targeting

from .errors import ApprovalRequired
from .results import VALIDATED, VALIDATION_FAILED, LaunchResult
from .saga import Saga
from .probe import standin_for
from .spec import LaunchSpec, cta_for, adset_params, campaign_params, creative_params, targeting_dict
from target_ai.storage import Plan

log = logging.getLogger(__name__)
TEST_MARK = "[ТЕСТ]"


class Validation(Saga):
    async def validate_plan_in_meta(self, plan: Plan, spec: LaunchSpec, actor: str) -> LaunchResult:
        """Проверяет план в Meta, ничего не оставляя: итог — план `validated` и пустой след в аккаунте."""
        action = await self._launch_action(plan, spec)
        try:
            await self.guard(action, plan.company_id, actor=actor)
        except ApprovalRequired:
            pass  # кнопка нужна для траты денег, а проверка ничего не тратит
        company = await self.io(self._db.companies.get, plan.company_id)
        snapshot = await self.snapshot(plan.company_id)
        cap = action.spend_cap
        temps: list[str] = []
        problems: list[str] = []
        try:
            await self._validate_all(company, spec, cap, snapshot.currency, temps, problems, actor, plan.id)
        finally:
            leftovers = await self._cleanup(temps, company.id, actor, plan.id)
        text = self._summary(spec, problems, leftovers)
        await self.audit(actor, "validate_plan", company.id, "error" if problems or leftovers else "ok",
                         plan_id=plan.id, problems=problems, leftovers=leftovers)
        if problems or leftovers:
            return LaunchResult(VALIDATION_FAILED, text, plan.id, created=tuple(leftovers))
        await self.io(self._db.plans.update, replace(plan, status="validated", updated_at=self._clock.now()))
        return LaunchResult(VALIDATED, text, plan.id)

    @staticmethod
    def _summary(spec: LaunchSpec, problems: list[str], leftovers: list[str]) -> str:
        if leftovers:
            return ("Проверка прошла, но тестовые объекты не удалось убрать: "
                    f"{', '.join(leftovers)}. Они на паузе; удалите их вручную.")
        if problems:
            return "Meta не приняла проверку: " + "; ".join(problems)
        return f"План «{spec.name}» проверен в Meta без создания объектов. Включение на тестовом уровне запрещено."

    async def _validate_all(self, company, spec, cap, currency, temps, problems, actor, plan_id) -> None:
        validator, real, account = self.gateway(validate_only=True), self.gateway(), company.ad_account_id
        positions = spec.meta.positions
        try:
            base = campaign_params(spec, f"{TEST_MARK} {spec.name}", cap, tuple(sorted({c for g in spec.groups for c in g.countries})))
            await self._check("campaign", problems, lambda: validator.create_campaign(account, base))
            await self.guard(CreateObject("campaign"), company.id, actor=actor)
            parent = self._tagged_params(base, plan_id, "campaign")
            campaign = await self.mutate(actor, company.id, "test:parent_campaign", {},
                                         lambda: real.create_campaign(account, parent, idempotency_key=str(uuid.uuid4())))
            temps.append(campaign)
            first_adset = None
            for n, group in enumerate(spec.groups, 1):
                params = adset_params(spec, group, name=f"{TEST_MARK} {group.name}", campaign_id=campaign,
                                      page_id=company.page_id, targeting=targeting_dict(spec, group, audiences={}))
                await self._check(f"adset:{n}", problems, lambda p=params: validator.create_adset(account, p))
                if first_adset is None:
                    await self.guard(CreateObject("group", None, group.daily_budget, group.goal_kind,  # type: ignore[arg-type]
                                                  Targeting.instagram_only(positions)), company.id, actor=actor)
                    parent = self._tagged_params(params, plan_id, "adset")
                    first_adset = await self.mutate(
                        actor, company.id, "test:parent_adset", {},
                        lambda p=parent: real.create_adset(account, p, idempotency_key=str(uuid.uuid4())))
                    temps.append(first_adset)
            await self._validate_ads(company, spec, validator, real, first_adset, temps, problems, actor, plan_id)
        except MetaError as exc:
            problems.append(exc.user_message_ru)

    async def _validate_ads(self, company, spec, validator, real, adset, temps, problems, actor, plan_id) -> None:
        account, creative_id = company.ad_account_id, None
        for n, group in enumerate(spec.groups, 1):
            for m, ad in enumerate(group.ads, 1):
                name = f"{TEST_MARK} {n}:{m}"
                params = creative_params(spec, ad, name=name, page_id=company.page_id or "",
                                         ig_user_id=company.ig_user_id or "", image_hash=None)
                if ad.kind == "new_creative":  # картинки в проверку не грузим: вместо неё настоящий пост или ссылка
                    params = await standin_for(real, name=name, page_id=company.page_id or "",
                                               ig_user_id=company.ig_user_id or "", website=spec.website_url,
                                               cta=cta_for(spec))
                await self._check(f"creative:{n}:{m}", problems, lambda p=params: validator.create_creative(account, p))
                if creative_id is None:
                    await self.guard(CreateObject("creative"), company.id, actor=actor)
                    parent = self._tagged_params(params, plan_id, "creative")
                    creative_id = await self.mutate(
                        actor, company.id, "test:parent_creative", {},
                        lambda p=parent: real.create_creative(account, p, idempotency_key=str(uuid.uuid4())))
                    temps.append(creative_id)
                ad_params: dict[str, Any] = {"name": f"{TEST_MARK} {n}:{m}", "adset_id": adset,
                                             "creative": {"creative_id": creative_id}, "status": "PAUSED"}
                await self._check(f"ad:{n}:{m}", problems, lambda p=ad_params: validator.create_ad(account, p))

    @staticmethod
    async def _check(label: str, problems: list[str], call) -> None:
        try:
            await call()
        except MetaError as exc:
            problems.append(f"{label}: {exc.user_message_ru}")

    @staticmethod
    def _tag_prefix(plan_id: int) -> str:
        return f"[ta:T{plan_id}:"

    def _tagged_params(self, params: dict[str, Any], plan_id: int, kind: str) -> dict[str, Any]:
        """Временный родитель несёт тег плана в имени: по нему его находят, даже если ответ о создании потерян."""
        return {**params, "name": f"{params['name']} {self._tag_prefix(plan_id)}{kind}]"}

    async def _cleanup(self, temps: list[str], company_id: int, actor: str, plan_id: int) -> list[str]:
        """Удаляет временные объекты в обратном порядке, затем ищет по тегу то, что осталось; возвращает неубранное."""
        for object_id in reversed(temps):
            await self._delete_temp(object_id, company_id, actor)
        account = (await self.io(self._db.companies.get, company_id)).ad_account_id
        left: list[str] = []
        for edge in ("campaigns", "adsets", "adcreatives"):
            try:
                rows = await self._tagged(account, edge, self._tag_prefix(plan_id))
            except Exception:
                log.exception("Не удалось проверить остатки «%s» по тегу", edge)
                left.append(f"{edge}: не удалось проверить по тегу")
                continue
            for row in rows:
                if not await self._delete_temp(str(row["id"]), company_id, actor):
                    left.append(str(row["id"]))
        return list(dict.fromkeys(left))

    async def _delete_temp(self, object_id: str, company_id: int, actor: str) -> bool:
        try:
            await self.mutate(actor, company_id, "test:delete_parent", {"object": object_id},
                              lambda: self.gateway().delete(object_id))
            return True
        except Exception:
            log.exception("Не удалось удалить тестовый объект %s", object_id)
            return False
