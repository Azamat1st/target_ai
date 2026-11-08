"""Модерация (история 45): опрос статусов, причина из Meta, исправление Копирайтера, проверенное Контролёром."""

from __future__ import annotations

import logging

from target_ai import policy
from target_ai.agents import AdForReview, ControlInput, CopyInput, Objective, PassportFact, control, write_copy
from target_ai.agents.schemas import AdVariant, Voice
from target_ai.executor import ExecutorError, PolicyDenied
from target_ai.llm import LLMError
from target_ai.meta.errors import MetaError
from target_ai.storage import Company, MetaObject

from .common import OpsContext

log = logging.getLogger(__name__)

CB_FIX, CB_SKIP = "ops:fix:", "ops:skipfix:"
APPROVAL_KIND = "creative_fix"
DISAPPROVED = "DISAPPROVED"
USER_SOURCE = "пользователь"


def disapproval_reason(status: dict) -> str:
    """Причина из `ad_review_feedback` (global / instagram) и `issues_info`."""
    feedback = status.get("ad_review_feedback") or {}
    parts = list((feedback.get("global") or {}).keys())
    for name in (feedback.get("placement_specific") or {}).get("instagram", {}) or {}:
        if name not in parts:
            parts.append(name)
    parts += [i.get("error_message") or i.get("error_summary") for i in status.get("issues_info") or []]
    return "; ".join(dict.fromkeys(p for p in parts if p)) or "причина не указана Meta"


class Moderation:
    def __init__(self, ctx: OpsContext) -> None:
        self._ctx = ctx

    async def check_all(self) -> int:
        return sum([await self.check_company(c.id) for c in await self._ctx.active_companies()])

    async def check_company(self, company_id: int) -> int:
        """Возвращает число новых уведомлений об отклонении."""
        ctx = self._ctx
        company = await ctx.company(company_id)
        ads = [o for o in await ctx.io(ctx.db.objects.list, company_id=company_id, kind="ad")
               if o.status not in ("DELETED", "ARCHIVED") and o.launch_id]
        handled = 0
        for ad in ads:
            try:
                status = await ctx.gateway().get_ad_status(ad.meta_id)
            except MetaError as exc:
                log.warning("Статус объявления %s: %s", ad.meta_id, exc.user_message_ru)
                continue
            if status.get("effective_status") == DISAPPROVED and await ctx.io(
                    ctx.db.alerts.mark, f"disapproved:{ad.meta_id}", ctx.clock.now()):
                await self._handle(company, ad, disapproval_reason(status))
                handled += 1
        return handled

    async def apply_fix(self, approval_id: str):
        """Кнопка «Применить исправление»: подтверждение хранит объявление и текст."""
        ctx = self._ctx
        approval = await ctx.io(ctx.db.approvals.get, approval_id)
        if approval is None:
            raise ExecutorError("Подтверждение не найдено.")
        variant = AdVariant.model_validate(approval.payload["variant"])
        return await ctx.executor.apply_creative_fix(approval.payload["ad_id"], variant, approval_id)

    # ------------------------------------------------------------------ внутреннее
    async def _handle(self, company: Company, ad: MetaObject, reason: str) -> None:
        ctx = self._ctx
        head = f"Объявление «{ad.name or ad.meta_id}» ({company.name}) отклонено Meta. Причина: {reason}."
        variant, note = await self._fix(company, reason)
        if variant is None:
            await ctx.emit(f"{head} Исправление подготовить не удалось: {note}.")
            return
        text = f"{head}\nИсправленный вариант: «{variant.headline}» — {variant.primary_text}"
        if company.mode == "autonomous":
            try:
                done = await ctx.executor.apply_creative_fix(ad.meta_id, variant, None, actor="optimizer")
            except (PolicyDenied, ExecutorError) as exc:
                await ctx.emit(f"{text}\nАвтоматически применить не вышло: {exc}")
                return
            await ctx.emit(f"{text}\n{done.message_ru}")
            return
        approval = await ctx.io(ctx.db.approvals.create, kind=APPROVAL_KIND, now=ctx.clock.now(),
                                company_id=company.id, payload={"ad_id": ad.meta_id, "variant": variant.model_dump()})
        await ctx.emit(text, [[("Применить исправление", CB_FIX + approval.id),
                               ("Пропустить", CB_SKIP + approval.id)]])

    async def _fix(self, company: Company, reason: str) -> tuple[AdVariant | None, str]:
        ctx = self._ctx
        if ctx.llm is None:
            return None, "нет доступа к Claude"
        passport = await ctx.io(ctx.db.passports.latest, company.id)
        facts = [f for f in await ctx.io(ctx.db.facts.list, company_id=company.id)]
        user_facts = tuple(PassportFact(text=f.text, source=f.source) for f in facts if f.source == USER_SOURCE)
        try:
            voice = Voice.model_validate((passport.data if passport else {}).get("voice") or {})
        except ValueError:
            voice = Voice(language="ru", address="вы", style="нейтральный")
        try:
            copy = await write_copy(ctx.llm, CopyInput(
                user_facts=user_facts, voice=voice, company_name=company.name, objective=Objective.DIRECT_MESSAGES,
                hypothesis="broad", feedback=f"Объявление отклонено Meta: {reason}. Перепиши без нарушения."))
            candidates = [v for v in copy.variants if v.kind == "new_creative"]
            for variant in candidates:
                if await self._approved(company, variant, voice, user_facts, [f.text for f in facts]):
                    return variant, ""
        except LLMError as exc:
            return None, exc.user_message_ru
        return None, "ни один вариант не прошёл проверку Контролёра"

    async def _approved(self, company, variant: AdVariant, voice, user_facts, fact_texts) -> bool:
        if policy.controller_check(variant.primary_text, headline=variant.headline, language=voice.language).findings:
            return False
        if policy.check_facts(variant.primary_text + " " + variant.headline, fact_texts):
            return False
        verdict = await control(self._ctx.llm, ControlInput(
            ads=(AdForReview(index=0, kind=variant.kind, text=variant.primary_text, headline=variant.headline,
                             cta=variant.cta),), user_facts=user_facts, language=voice.language))
        status = verdict.ads[0].status if verdict.ads else "violation"
        return status == "ok" or (status == "needs_review" and bool(company.profile.get("demo")))
