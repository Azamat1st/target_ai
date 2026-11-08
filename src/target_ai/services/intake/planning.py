"""Медиаплан (R18–R24, R36, R38, R66, R67): Стратег -> интересы -> оценки -> Копирайтер -> Контролёр -> validate_plan."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

from target_ai.agents import (
    CopyInput, CreativeOption, InterestOption, Objective, Passport, PassportFact, Questionnaire, StrategyInput, strategize,
)
from target_ai.executor import ExecutorError, PolicyDenied, plan_data_from_validated
from target_ai.llm.errors import LLMError
from target_ai.meta.errors import MetaError
from target_ai.agents.schemas import NOT_FOUND
from target_ai.money import Money, MoneyError, format_ru, from_major
from target_ai.policy import PlanContext, validate_plan
from target_ai.policy.rules import MIN_RETARGETING_AUDIENCE
from target_ai.storage import MediaAsset, Plan

from .audience import DEMO_SEED_KEYWORDS, build_catalog, estimate_size, seed_keywords
from .copy_flow import write_reviewed_copy
from .media import store_file
from .plan_view import render_plan
from .planning_ui import KIND_EDIT, KIND_CANCEL, KIND_GO, KIND_START, RESULT_NOUN, STATE_CPA, PlanInteractions
from .texts import button

log = logging.getLogger(__name__)

DEMO_CREATIVE = "[демо-креатив]"
_PLACEHOLDER_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f0005000101"
    "a5f645400000000049454e44ae426082")
MIN_GROUPS_FOR_BUDGET = 2
MAX_GROUPS_CAP = 4
PROFILE_URL = "https://instagram.com/{handle}"


@dataclass(frozen=True)
class PlanOutcome:
    status: str  # proposed | blocked | needs_input | failed | launched
    plan_id: int | None = None
    message: str = ""


class Planning(PlanInteractions):
    def __init__(self, db, gateway_factory, llm, clock, notifier, executor, *, media_dir: Path) -> None:
        self._db, self._factory, self._llm = db, gateway_factory, llm
        self._clock, self._notifier, self._executor, self._dir = clock, notifier, executor, media_dir

    async def propose(self, company_id: int, chat_id: int, *, edit_request: str | None = None) -> PlanOutcome:
        company = await self._io(self._db.companies.get, company_id)
        row = await self._io(self._db.passports.latest, company_id) if company else None
        if company is None or row is None:
            return await self._stop(chat_id, "needs_input", "Сначала нужен паспорт компании — запустите анализ.")
        passport = Passport.model_validate(row.data)
        objective = Objective(company.profile.get("objective") or passport.recommended_objective)
        if await self._preflight(company, objective, chat_id, edit_request):
            return PlanOutcome("needs_input")
        gateway = self._factory(False)
        objective_note, website_url = None, company.website
        if objective is Objective.PROFILE_TRAFFIC and not await self._profile_traffic_ok(gateway, company):
            objective, website_url = Objective.WEBSITE_TRAFFIC, PROFILE_URL.format(handle=company.instagram_handle)
            objective_note = ("Трафик в профиль недоступен для этого аккаунта — веду трафик на сайт со ссылкой на "
                              f"профиль {website_url}.")
        try:
            snapshot = await self._executor.build_snapshot(company_id)
        except (ExecutorError, MetaError) as exc:
            return await self._stop(chat_id, "failed", f"Не могу посчитать бюджет: {getattr(exc, 'user_message_ru', None) or exc}")
        assets = await self._creatives(company)
        if assets is None:
            return await self._stop(chat_id, "needs_input", "Для объявлений нужны фото или видео: пришлите их в чат (до 20 МБ), "
                                    "и я соберу медиаплан.")
        facts = await self._io(self._db.facts.list, company_id=company_id)
        user_facts = tuple(PassportFact(text=f.text, source=f.source) for f in facts if f.kind == "user")
        external = tuple(PassportFact(text=f.text, source=f.source) for f in facts if f.kind != "user")
        history = await self._history(company_id)
        countries = tuple(company.profile.get("countries", ()))
        engagement, engagement_note = await self._engagement(company_id)
        target, target_line = self._target(company, passport)
        minimum = snapshot.min_budget_imp if objective is Objective.REACH else snapshot.min_budget_high_freq
        max_groups = max(MIN_GROUPS_FOR_BUDGET, min(MAX_GROUPS_CAP, snapshot.daily_cap.amount_minor // (minimum.amount_minor or 1)))
        questionnaire = Questionnaire(company_name=company.name, instagram_handle=company.instagram_handle, website=company.website,
                                      geo=company.profile.get("geo"), objective=objective, notes=edit_request)
        has_audience = engagement is not None and engagement >= MIN_RETARGETING_AUDIENCE
        try:
            interests = await self._discover_interests(gateway, company, passport, facts, objective, countries, edit_request)
            proposal = await strategize(self._llm, StrategyInput(
                passport=passport, user_facts=user_facts, external_facts=external, questionnaire=questionnaire,
                daily_cap=snapshot.daily_cap, min_group_budget=minimum, interests=interests, history=history, max_groups=max_groups,
                retargeting_available=has_audience, lookalike_available=engagement is not None))
            proposal = proposal.model_copy(update={"objective": objective})
            keywords = [k for g in proposal.groups for k in g.interest_keywords]
            catalog, search_notes = await build_catalog(gateway, company.ad_account_id, objective, company.page_id, countries, keywords)
            broad = await estimate_size(gateway, company.ad_account_id, objective, company.page_id, countries)
            copies, verdicts, copy_notes = [], [], []
            for group in proposal.groups:
                copy, verdict, notes = await write_reviewed_copy(
                    self._llm, self._copy_input(company, passport, objective, group, user_facts, external, assets[0], edit_request),
                    {"targeting": {"countries": list(countries), "hypothesis": group.hypothesis.value},
                     "user_facts": user_facts, "external_facts": external, "language": passport.voice.language, "objective": objective},
                    fact_texts=[f.text for f in facts], captions=assets[2], label=group.name,
                    post_ids=assets[4], uploaded=assets[5])
                copies.append(copy), verdicts.append(verdict), copy_notes.extend(notes)
        except LLMError as exc:
            retry = button(self._db, self._clock.now(), "Повторить", KIND_START, "start", company_id, {"company_id": company_id})
            reason = getattr(exc, "user_message_ru", None) or "Не удалось получить ответ от Claude."
            return await self._stop(chat_id, "failed", f"{reason}\nДанные сохранены — можно повторить.", [[retry]])
        ctx = PlanContext(
            currency=snapshot.currency, daily_cap=snapshot.daily_cap, min_budget_imp=snapshot.min_budget_imp,
            min_budget_high_freq=snapshot.min_budget_high_freq, active_daily_total=Money(int(snapshot.active_total), snapshot.currency),
            interest_catalog=catalog, engagement_audience_size=engagement, lookalike_source_size=engagement,
            broad_audience_size=broad, facts=tuple(f.text for f in facts), creative_ids=frozenset(assets[1]),
            has_uploaded_media=assets[3], language=passport.voice.language,
            company_texts=(passport.offer, passport.usp, company.name), default_countries=countries,
            fallback_objective=objective.value, demo=bool(company.profile.get("demo")))
        validated = validate_plan(proposal, ctx, copy=copies, verdicts=verdicts)
        extra = [engagement_note] if engagement_note else []
        data = plan_data_from_validated(validated, name=company.name, website_url=website_url)
        data.update(privacy_policy_url=company.profile.get("privacy_url"), edit_request=edit_request,
                    dsa_beneficiary=(company.profile.get("dsa") or {}).get("beneficiary"),
                    dsa_payor=(company.profile.get("dsa") or {}).get("payor"),
                    target_cpa=({"amount_minor": target.amount_minor, "currency": target.currency} if target else None),
                    notes=[*validated.notes_ru, *search_notes, *copy_notes, *extra], blockers=list(validated.blockers_ru))
        plan = await self._io(self._store, company_id, data, "proposed" if validated.ok else "draft")
        text = render_plan(
            company_name=company.name, version=plan.version, validated=validated, extra_notes=[*search_notes, *copy_notes, *extra],
            history=history, monthly_limit=company.monthly_limit, expected_cpa=target_line,
            spend_cap_warning=self._cap_warning(snapshot), objective_note=objective_note)
        return await self._present(chat_id, company, plan, text, validated.ok, target)

    # ------------------------------------------------------------------ показ плана
    async def _present(self, chat_id: int, company, plan: Plan, text: str, ok: bool, target: Money | None) -> PlanOutcome:
        row = [self._plan_button("✏️ Изменить", KIND_EDIT, "edit", company.id, plan.id),
               self._plan_button("✖️ Отмена", KIND_CANCEL, "cancel", company.id, plan.id)]
        approvals = {"edit": row[0][1].split(":")[1], "cancel": row[1][1].split(":")[1]}
        if not ok:
            await self._io(self._save_approvals, plan, approvals)
            await self._notifier.send(chat_id, f"{text}\nЗапустить такой план нельзя. Измените запрос или отмените.", [row])
            return PlanOutcome("blocked", plan.id, text)
        if target is None:
            await self._io(self._save_approvals, plan, approvals)
            noun = RESULT_NOUN[Objective(plan.data["objective"])]
            await self._ask(chat_id, STATE_CPA, {"company_id": company.id, "plan_id": plan.id},
                            f"{text}\n\nКакая цена {noun} вас устроит? Например: 2 евро. Без цены план на запуск не уходит.", [row])
            return PlanOutcome("needs_input", plan.id, text)
        return await self._offer_launch(chat_id, company, plan, text, row, approvals)

    async def _offer_launch(self, chat_id: int, company, plan: Plan, text: str, row: list, approvals: dict) -> PlanOutcome:
        try:
            verdict = await self._executor.check_launch(plan.id)
        except PolicyDenied as exc:
            await self._io(self._save_approvals, plan, approvals)
            if await self._ask_missing(chat_id, company, exc.code, plan.data.get("edit_request")):
                return PlanOutcome("needs_input", plan.id, text)
            await self._notifier.send(chat_id, f"{text}\n⛔ {exc.reason_ru}", [row])
            return PlanOutcome("blocked", plan.id, text)
        except (ExecutorError, ValueError) as exc:
            await self._io(self._save_approvals, plan, approvals)
            await self._notifier.send(chat_id, f"{text}\nПлан не проходит проверку запуска: {exc}", [row])
            return PlanOutcome("blocked", plan.id, text)
        if verdict.needs_approval is not None:
            go = await self._io(self._executor.create_approval, verdict.needs_approval, company_id=company.id)
            approvals["go"] = go.id
            go_button = ("🚀 Запустить", f"ap:{go.id}:go")
        elif company.mode == "autonomous":
            await self._io(self._save_approvals, plan, approvals)
            await self._notifier.send(chat_id, f"{text}\nАвтономный режим: запускаю.", [])
            result = await self._executor.launch(plan.id)
            await self._notifier.send(chat_id, result.message_ru, [])
            if result.status not in ("needs_approval", "busy"):
                await self._accept_target(plan)
            return PlanOutcome("launched", plan.id, result.message_ru)
        else:
            go_button = self._plan_button("🚀 Запустить", KIND_GO, "go", company.id, plan.id)
            approvals["go"] = go_button[1].split(":")[1]
        await self._io(self._save_approvals, plan, approvals)
        await self._notifier.send(chat_id, text, [[go_button, *row]])
        return PlanOutcome("proposed", plan.id, text)

    def _store(self, company_id: int, data: dict, status: str) -> Plan:
        """Новая версия плана; предыдущие неподтверждённые устаревают вместе со своими кнопками."""
        with self._db.transaction():
            latest = self._db.plans.latest(company_id)
            now = self._clock.now()
            plan = self._db.plans.create(Plan(company_id=company_id, version=(latest.version + 1) if latest else 1,
                                              status=status, data=data, created_at=now, updated_at=now))
            for old in self._db.plans.list(company_id=company_id):
                if old.id != plan.id and old.status in ("proposed", "draft"):
                    for approval_id in old.data.get("approvals", {}).values():
                        self._db.approvals.cancel(approval_id)
                    self._db.plans.update(replace(old, status="superseded", updated_at=now))
        return plan

    def _save_approvals(self, plan: Plan, approvals: dict) -> None:
        current = self._db.plans.get(plan.id) or plan
        self._db.plans.update(replace(current, data={**current.data, "approvals": approvals}, updated_at=self._clock.now()))

    # ------------------------------------------------------------------ входные данные
    async def _creatives(self, company):
        """(варианты для Копирайтера, допустимые id, подписи по id, есть ли присланные медиа) или None — нужны фото/видео."""
        assets = await self._io(self._db.media.list, company_id=company.id)
        uploaded = [a for a in assets if a.kind in ("image", "video")]
        posts = [a for a in assets if a.kind == "ig_post" and a.meta_id]
        if not uploaded and not posts:
            if not company.profile.get("demo"):
                return None
            uploaded = [await self._io(self._demo_creative, company.id) for _ in range(2)]
        # Копирайтеру — только посты (кандидаты existing_post); присланные медиа к new_creative привязывает код.
        options = [CreativeOption(id=a.meta_id, kind="post", caption=a.caption or "", score=a.data.get("engagement")) for a in posts]
        ids = [*(o.id for o in options), *(str(a.id) for a in uploaded)]
        return (tuple(options), ids, {o.id: o.caption for o in options}, bool(uploaded),
                {a.meta_id for a in posts}, [str(a.id) for a in uploaded])

    def _demo_creative(self, company_id: int) -> MediaAsset:
        path = store_file(self._dir, company_id, _PLACEHOLDER_PNG, "demo.png")
        return self._db.media.create(MediaAsset(company_id=company_id, kind="image", path=str(path),
                                                caption=DEMO_CREATIVE, created_at=self._clock.now()))

    @staticmethod
    def _copy_input(company, passport, objective, group, user_facts, external, creatives, edit_request) -> CopyInput:
        segment = passport.segments[0].name if passport.segments else None
        return CopyInput(user_facts=user_facts, external_facts=external, voice=passport.voice, company_name=company.name,
                         objective=objective, hypothesis=f"{group.hypothesis.value}: {group.name}. {group.rationale}",
                         segment=segment, creatives=creatives, feedback=edit_request)

    async def _discover_interests(self, gateway, company, passport, facts, objective, countries,
                                  edit_request) -> tuple[InterestOption, ...]:
        """Интересы из поиска Meta по словам компании — до Стратега, чтобы он выбирал из найденного, а не из головы."""
        texts = [company.name, edit_request or "", *(f.text for f in facts), passport.offer, passport.audience]
        words = seed_keywords(t for t in texts if t and NOT_FOUND not in t)
        catalog, _ = await build_catalog(gateway, company.ad_account_id, objective, company.page_id, countries, words)
        if not catalog and company.profile.get("demo"):
            catalog, _ = await build_catalog(gateway, company.ad_account_id, objective, company.page_id, countries,
                                             DEMO_SEED_KEYWORDS)
        found = {i.name: i.audience_size for infos in catalog.values() for i in infos}
        return tuple(InterestOption(name=name, audience_size=size) for name, size in found.items())

    async def _history(self, company_id: int) -> tuple[str, ...]:
        rows = await self._io(self._db.history.list, company_id=company_id)
        return tuple(f"{r.hypothesis or r.kind}: {r.outcome or 'без итога'}" for r in rows)

    async def _profile_traffic_ok(self, gateway, company) -> bool:
        size = await estimate_size(gateway, company.ad_account_id, Objective.PROFILE_TRAFFIC, company.page_id,
                                   company.profile.get("countries", ()))
        return size is not None

    async def _engagement(self, company_id: int) -> tuple[int | None, str | None]:
        """Размер аудитории вовлечённых или причина, почему ретаргетинга и похожих не будет."""
        try:
            audience = await self._executor.ensure_engagement_audience(company_id)
        except MetaError as exc:
            if exc.kind == "audience_terms":
                return None, "ретаргетинг недоступен: примите условия пользовательских аудиторий в Ads Manager"
            return None, f"ретаргетинг недоступен: {exc.user_message_ru}"
        except (ExecutorError, PolicyDenied) as exc:
            return None, f"ретаргетинг недоступен: {getattr(exc, 'reason_ru', None) or exc}"
        if audience.size is None:
            return None, audience.note or "ретаргетинг недоступен: размер аудитории вовлечённых пока неизвестен"
        return audience.size, None

    @staticmethod
    def _target(company, passport) -> tuple[Money | None, str | None]:
        """Целевая цена: ваша, иначе оценка Аналитика; нет ни той ни другой -> (None, None)."""
        if company.target_cpa is not None:
            return company.target_cpa, f"{format_ru(company.target_cpa)} — ваша"
        if passport.expected_cpa is None or passport.expected_cpa <= 0 or not company.currency:
            return None, None
        try:
            money = from_major(Decimal(passport.expected_cpa), company.currency)
        except MoneyError:
            return None, None
        why = "" if passport.cpa_rationale in ("", NOT_FOUND) else f" ({passport.cpa_rationale})"
        return money, f"{format_ru(money)} — оценка Аналитика{why}"

    @staticmethod
    def _cap_warning(snapshot) -> str | None:
        floor = snapshot.min_spend_cap
        if floor is None or snapshot.remaining >= floor:
            return None
        return (f"Защитный лимит в Meta — {format_ru(floor)} (меньше Meta не позволяет), остаток вашего лимита — "
                f"{format_ru(snapshot.remaining)}; разницу контролирует бот.")

    async def _stop(self, chat_id: int, status: str, text: str, buttons=None) -> PlanOutcome:
        await self._notifier.send(chat_id, text, buttons or [])
        return PlanOutcome(status, None, text)
