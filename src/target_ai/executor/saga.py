"""Сага запуска: шаги, идемпотентность, восстановление, откат (спека §4, §6 «Без дублей»)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from target_ai.meta.errors import MetaError
from target_ai.money import Money
from target_ai.policy import (
    Activate, CreateObject, Delete, Launch, NewGroup, Pause, SetSpendCap, Targeting,
)
from target_ai.storage import Company, Launch as LaunchRow, LaunchStep, Plan

from .core import DEAD_STATUSES, RETIRED, AccountInfo, Core
from .errors import ApprovalInvalid, ApprovalRequired, ExecutorError, PolicyDenied, StepFailed
from .results import (
    ALREADY_DONE, AWAITING_DECISION, BUSY, BUTTON_CONTINUE, BUTTON_RETRY, BUTTON_ROLLBACK, LAUNCHED,
    NEEDS_APPROVAL, ROLLED_BACK, LaunchResult, RecoveryItem,
)
from .spec import (
    LaunchSpec, adset_params, campaign_params, creative_params, leadform_params, missing_requirement, money_json,
    parse_spec, step_names, tag_for, targeting_dict, RETENTION_SECONDS_YEAR,
)
from target_ai.config.runtime import DEFAULT_LEVEL, SETTING_LEVEL

log = logging.getLogger(__name__)

OPEN_LAUNCH = ("started", "in_progress", "awaiting_decision")
LAUNCHABLE_PLAN = ("approved", "proposed")
KILL_ORDER = ("ad", "creative", "leadform", "adset", "audience", "campaign")
CREATE_RETRIES = 2
PAUSE_ATTEMPTS = 2
PAUSABLE = ("campaign", "adset", "ad")
CLOCK_SKEW = timedelta(minutes=2)  # допуск расхождения часов Meta и нашего
BUSY_TEXT = "По этой компании уже идёт запуск: дождитесь его завершения или откатите его."


@dataclass
class Run:
    """Состояние одного прохода саги (создание или сверка)."""

    launch: LaunchRow
    company: Company
    spec: LaunchSpec
    info: AccountInfo
    actor: str
    create: bool
    install: str = ""
    ids: dict[str, str] = field(default_factory=dict)
    unpaused: list[str] = field(default_factory=list)  # что не удалось поставить на паузу после сбоя активации

    @property
    def account(self) -> str:
        return self.company.ad_account_id or ""

    def tag(self, step: str) -> str:
        return tag_for(self.install, self.launch.id, step)

    def name(self, base: str, step: str) -> str:
        return f"{base} {self.tag(step)}"


def created_after(sent_at: Any, created: Any) -> bool:
    """Объект создан не раньше записи о попытке (с допуском на часы): ISO-строка или unix-время Meta."""
    if not sent_at or not created:
        return False
    try:
        sent = datetime.fromisoformat(str(sent_at))
        born = (datetime.fromtimestamp(created, UTC) if isinstance(created, int | float)
                else datetime.fromisoformat(str(created)))
    except ValueError:
        return False
    born = born if born.tzinfo else born.replace(tzinfo=UTC)
    return born >= sent - CLOCK_SKEW


class Saga(Core):
    # ------------------------------------------------------------------ вход
    async def check_launch(self, plan_id: int) -> LaunchResult:
        """Проверка перед кнопкой: `ready` или `needs_approval` (с ключом); отказ — `PolicyDenied`."""
        plan, spec = await self._plan_and_spec(plan_id)
        self._require_complete(spec)
        action = await self._launch_action(plan, spec)
        try:
            await self.guard(action, plan.company_id, retiring=await self._retiring(plan.company_id))
        except ApprovalRequired as need:
            return LaunchResult(NEEDS_APPROVAL, need.needs.summary_ru, plan_id, needs_approval=need.needs)
        return LaunchResult("ready", "Запуск разрешён политикой.", plan_id)

    async def launch(self, plan_id: int, approval_id: str | None = None, *, actor: str = "user") -> LaunchResult:
        plan, spec = await self._plan_and_spec(plan_id)
        existing = await self.io(self._db.launches.get_by_plan, plan_id)
        if existing:
            return self._already(existing)
        self._require_complete(spec)
        if self._level() == "test":
            return await self.validate_plan_in_meta(plan, spec, actor)  # type: ignore[attr-defined]
        if plan.status not in LAUNCHABLE_PLAN:
            raise ExecutorError(f"План в статусе «{plan.status}» запускать нельзя.")
        action = await self._launch_action(plan, spec)
        keys = await self.io(self.peek_keys, approval_id, plan.company_id, ("launch",))
        try:
            await self.guard(action, plan.company_id, actor=actor, approved=keys,
                             retiring=await self._retiring(plan.company_id))
        except ApprovalRequired as need:
            return LaunchResult(NEEDS_APPROVAL, need.needs.summary_ru, plan_id, needs_approval=need.needs)
        async with self.company_lock(plan.company_id):
            launch = await self.io(self._begin, plan, approval_id)
        if launch.error == "busy":
            return LaunchResult(BUSY, BUSY_TEXT, plan_id)
        if launch.status != "started" or launch.error == "exists":
            return self._already(launch)
        await self.audit(actor, "launch:start", plan.company_id, "ok", plan_id=plan_id, launch_id=launch.id)
        refused = await self._recheck(launch, plan, spec, actor, keys)
        if refused is not None:
            return refused
        return await self._run(launch, plan, spec, actor, create=True)

    async def _recheck(self, launch: LaunchRow, plan: Plan, spec: LaunchSpec, actor: str,
                       keys: frozenset[str]) -> LaunchResult | None:
        """§4 шаг 2: лимиты на текущих данных уже после потребления подтверждения; не прошло — ничего не создаём."""
        try:
            await self.guard(await self._launch_action(plan, spec), plan.company_id, actor=actor, approved=keys,
                             retiring=await self._retiring(plan.company_id))
            return None
        except PolicyDenied as exc:
            reason = exc.reason_ru
        except ApprovalRequired as exc:
            reason = f"условия изменились после подтверждения: {exc.needs.summary_ru}"
        text = (f"Запуск «{spec.name}» не начат: {reason} Ничего не создано. "
                "Соберите медиаплан заново на текущих лимитах.")
        await self._set_launch(launch, "rolled_back", reason, finished=True)
        await self.io(self._db.plans.update, replace(plan, status="cancelled", updated_at=self._clock.now()))
        await self.audit(actor, "launch:recheck", plan.company_id, "denied", plan_id=plan.id, reason=reason)
        return LaunchResult(ROLLED_BACK, text, plan.id, launch.id)

    def _begin(self, plan: Plan, approval_id: str | None) -> LaunchRow:
        """Одна транзакция: потребить подтверждение и вставить запуск (UNIQUE по плану)."""
        with self._db.transaction():
            existing = self._db.launches.get_by_plan(plan.id)
            if existing:
                return replace(existing, error="exists")
            if any(x.company_id == plan.company_id and x.status in OPEN_LAUNCH for x in self._db.launches.list()):
                return LaunchRow(plan_id=plan.id, company_id=plan.company_id, status="busy", error="busy")
            self.consume(approval_id)
            return self._db.launches.create(LaunchRow(
                plan_id=plan.id, company_id=plan.company_id, status="started", started_at=self._clock.now()))

    async def continue_launch(self, launch_id: int, *, actor: str = "user") -> LaunchResult:
        """«Довести» / «Повторить»: создаёт только недостающее и включает."""
        launch, plan, spec = await self._open_launch(launch_id)
        return await self._run(launch, plan, spec, actor, create=True)

    async def rollback_launch(self, launch_id: int, *, actor: str = "user") -> LaunchResult:
        launch = await self.io(self._db.launches.get, launch_id)
        if launch is None:
            raise ExecutorError(f"Запуск {launch_id} не найден.")
        if launch.status == "rolled_back":
            return LaunchResult(ROLLED_BACK, "Запуск уже откатан.", launch.plan_id, launch_id)
        plan = await self.io(self._db.plans.get, launch.plan_id)
        run = await self._make_run(launch, plan, parse_spec(plan.data), actor, create=False)
        await self._reconcile(run)
        left = await self._delete_all(run)
        return await self._finish_rollback(launch, plan, left)

    async def recover_incomplete(self) -> list[RecoveryItem]:
        """При старте: сверка прерванных запусков с Meta, пауза созданного, вопрос «Довести/Откатить»."""
        launches = await self.io(lambda: [x for x in self._db.launches.list() if x.status in OPEN_LAUNCH])
        items = []
        for launch in launches:
            plan = await self.io(self._db.plans.get, launch.plan_id)
            spec = parse_spec(plan.data)
            run = await self._make_run(launch, plan, spec, "system", create=False)
            note = await self._reconcile(run)
            await self._pause_launch_objects(launch, everything=True)  # статус в реестре мог отстать от Meta
            items.append(await self._announce_recovery(launch, spec, note))
        return items

    # ------------------------------------------------------------------ подготовка
    def _level(self) -> str:
        return self._db.settings.get(SETTING_LEVEL, DEFAULT_LEVEL)

    @staticmethod
    def _require_complete(spec: LaunchSpec) -> None:
        """Нет данных, без которых Meta откажет (ссылка на политику, DSA-поля) - отказ до создания чего-либо."""
        missing = missing_requirement(spec)
        if missing is not None:
            raise PolicyDenied(missing[1], missing[0])

    async def _plan_and_spec(self, plan_id: int) -> tuple[Plan, LaunchSpec]:
        plan = await self.io(self._db.plans.get, plan_id)
        if plan is None:
            raise ExecutorError(f"Медиаплан {plan_id} не найден.")
        try:
            return plan, parse_spec(plan.data)
        except ValueError as exc:
            raise ExecutorError(str(exc)) from exc

    async def _open_launch(self, launch_id: int) -> tuple[LaunchRow, Plan, LaunchSpec]:
        launch = await self.io(self._db.launches.get, launch_id)
        if launch is None or launch.status not in OPEN_LAUNCH:
            raise ExecutorError("Этот запуск уже завершён или не найден.")
        plan, spec = await self._plan_and_spec(launch.plan_id)
        return launch, plan, spec

    async def _make_run(self, launch: LaunchRow, plan: Plan, spec: LaunchSpec, actor: str, *, create: bool) -> Run:
        company = await self.io(self._db.companies.get, launch.company_id)
        return Run(launch, company, spec, await self.account_info(company), actor, create, await self.io(self.install_id))

    def launch_groups(self, spec: LaunchSpec) -> tuple[NewGroup, ...]:
        positions = spec.meta.positions
        return tuple(NewGroup(g.daily_budget, g.goal_kind, Targeting.instagram_only(positions), id=f"g{n}")  # type: ignore[arg-type]
                     for n, g in enumerate(spec.groups, 1))

    async def _launch_action(self, plan: Plan, spec: LaunchSpec) -> Launch:
        snapshot = await self.snapshot(plan.company_id)
        cap = snapshot.remaining
        if snapshot.min_spend_cap is not None and cap < snapshot.min_spend_cap:
            cap = snapshot.min_spend_cap  # меньше минимума Meta не позволяет: политика потребует кнопку
        return Launch(groups=self.launch_groups(spec), spend_cap=cap, tag=f"P{plan.id}")

    async def _retiring(self, company_id: int) -> frozenset[str]:
        """Кампании, которые `retire_previous` снимет первым шагом: для проверки запуска они уже неактивны."""
        return await self.io(lambda: frozenset(
            o.meta_id for o in self._previous_campaigns(company_id, None)))

    @staticmethod
    def _already(launch: LaunchRow) -> LaunchResult:
        return LaunchResult(ALREADY_DONE, "Этот запуск уже выполнен или идёт — повторно не создаю.",
                            launch.plan_id, launch.id)

    # ------------------------------------------------------------------ проход
    async def _run(self, launch: LaunchRow, plan: Plan, spec: LaunchSpec, actor: str, *, create: bool) -> LaunchResult:
        run = await self._make_run(launch, plan, spec, actor, create=create)
        await self._set_launch(launch, "in_progress", None)
        try:
            await self._execute(run)
        except StepFailed as exc:
            return await self._on_failure(run, exc)
        launch = await self._set_launch(launch, "launched", None, finished=True)
        await self.io(self._db.plans.update, replace(plan, status="launched", updated_at=self._clock.now()))
        created = self._created(run)
        await self.audit(actor, "launch:done", launch.company_id, "ok", launch_id=launch.id)
        return LaunchResult(LAUNCHED, "Кампания создана и включена; объявления уходят на модерацию Meta.",
                            plan.id, launch.id, run.ids.get("campaign"), created=created)

    async def _execute(self, run: Run) -> None:
        try:
            await self._retire_guarded(run)
            if not await self._step_campaign(run):
                return
            await self._step_audiences(run)
            await self._step_adsets(run)
            await self._step_media(run)
            await self._step_leadform(run)
            await self._step_creatives(run)
            await self._step_ads(run)
            if run.create:
                await self._step_activate(run)
        except PolicyDenied as exc:
            raise StepFailed("policy", exc.reason_ru) from exc
        except ApprovalRequired as exc:
            raise StepFailed("policy", exc.message_ru) from exc

    async def _set_launch(self, launch: LaunchRow, status: str, error: str | None, *, finished: bool = False) -> LaunchRow:
        updated = replace(launch, status=status, error=error,
                          finished_at=self._clock.now() if finished else launch.finished_at)
        await self.io(self._db.launches.update, updated)
        return updated

    def _created(self, run: Run) -> tuple[str, ...]:
        rows = self._db.objects.list(launch_id=run.launch.id)
        return tuple(o.meta_id for o in rows)

    async def _progress(self, run: Run) -> tuple[int, int, tuple[str, ...]]:
        names = step_names(run.spec)
        steps = await self.io(lambda: {s.step: s for s in self._db.launch_steps.list(launch_id=run.launch.id)})
        number = next((i for i, n in enumerate(names, 1) if not (n in steps and steps[n].status == "done")), len(names))
        return number, len(names), await self.io(self._created, run)

    async def _on_failure(self, run: Run, exc: StepFailed) -> LaunchResult:
        launch = await self._set_launch(run.launch, AWAITING_DECISION, exc.message_ru)
        number, total, created = await self._progress(run)
        text = (f"Запуск «{run.spec.name}» остановился на шаге {number} из {total} ({exc.step}): {exc.message_ru} "
                f"{self._pause_report(run)} Что делаем?")
        await self.audit(run.actor, "launch:failed", launch.company_id, "error", launch_id=launch.id,
                         step=exc.step, message=exc.message_ru, fbtrace_id=exc.fbtrace_id)
        await self.notify(text, [[(BUTTON_RETRY, f"launch:retry:{launch.id}"),
                                  (BUTTON_ROLLBACK, f"launch:rollback:{launch.id}")]])
        return LaunchResult(AWAITING_DECISION, text, launch.plan_id, launch.id, run.ids.get("campaign"),
                            exc.step, created)

    @staticmethod
    def _pause_report(run: Run) -> str:
        if run.unpaused:
            return (f"НЕ УДАЛОСЬ поставить на паузу: {', '.join(run.unpaused)} - они могут тратить деньги. "
                    "Остановите их вручную (кнопка «Стоп-кран») и повторите.")
        return "Всё созданное стоит на паузе и денег не тратит."

    async def _announce_recovery(self, launch: LaunchRow, spec: LaunchSpec, note: str) -> RecoveryItem:
        steps = await self.io(lambda: {s.step: s for s in self._db.launch_steps.list(launch_id=launch.id)})
        names = step_names(spec)
        number = next((i for i, n in enumerate(names, 1) if not (n in steps and steps[n].status == "done")), len(names))
        created = await self.io(lambda: tuple(o.meta_id for o in self._db.objects.list(launch_id=launch.id)))
        text = (f"Запуск «{spec.name}» прервался на шаге {number} из {len(names)}. "
                f"Создано: {', '.join(created) or 'ничего'}. {note}Всё созданное стоит на паузе. "
                "Довести запуск или откатить?")
        await self._set_launch(launch, AWAITING_DECISION, f"Прерван на шаге {number}.")
        await self.notify(text, [[(BUTTON_CONTINUE, f"launch:continue:{launch.id}"),
                                  (BUTTON_ROLLBACK, f"launch:rollback:{launch.id}")]])
        return RecoveryItem(launch.id, launch.company_id, number, len(names), created, text)

    async def _reconcile(self, run: Run) -> str:
        """Сверка реестра с Meta по тегам: найденное усыновляется; ничего не создаётся."""
        try:
            await self._execute(run)
        except StepFailed as exc:
            return f"Сверить с Meta полностью не удалось ({exc.message_ru}). "
        return ""

    # ------------------------------------------------------------------ шаги
    def _row(self, launch_id: int, step: str) -> LaunchStep:
        rows = self._db.launch_steps.list(launch_id=launch_id, step=step, limit=1)
        if rows:
            return rows[0]
        return self._db.launch_steps.create(LaunchStep(launch_id=launch_id, step=step, updated_at=self._clock.now()))

    def _save_row(self, row: LaunchStep, **changes: Any) -> LaunchStep:
        updated = replace(row, updated_at=self._clock.now(), **changes)
        self._db.launch_steps.update(updated)
        return updated

    def _mark_failed(self, launch_id: int, step: str, error: str) -> None:
        latest = self._row(launch_id, step)  # свежая строка: в ней ключ идемпотентности и время его записи
        self._save_row(latest, status="failed", data={**latest.data, "error": error})

    async def _find_existing(self, run: Run, step: str, lookup: tuple[str, str] | None) -> dict[str, Any] | None:
        """Реестр → Meta по тегу. Объект из Meta свой, только если мы точно слали создание и он не старше ключа."""
        tag = run.tag(step)
        known = await self.io(lambda: [o for o in self._db.objects.list(launch_id=run.launch.id) if tag in o.name])
        if known:
            return {"id": known[0].meta_id, "status": known[0].status, "name": known[0].name}
        if lookup is None:
            return None
        rows = await self._tagged(lookup[0], lookup[1], tag)
        if not rows:
            return None
        row = await self.io(self._row, run.launch.id, step)
        ours = next((r for r in rows if self._is_ours(row, r)), None)
        if ours is not None:
            return ours
        raise StepFailed(step, f"В аккаунте есть объект с таким же тегом ({rows[0]['id']}) — создан не системой. "
                               "Я его не трогаю.")

    async def _tagged(self, parent_id: str, edge: str, tag: str) -> list[dict[str, Any]]:
        gateway = self.gateway()
        if edge == "leadgen_forms":  # формы Страницы читаются токеном Страницы
            rows = await gateway.list_leadgen_forms(parent_id, await gateway.get_page_token(parent_id))
            return [row for row in rows if tag in str(row.get("name", ""))]
        return await gateway.find_by_name_tag(parent_id, edge, tag)

    async def _create_form(self, run: Run, params: dict[str, Any], key: str) -> str | None:
        gateway, page_id = self.gateway(), run.company.page_id or ""
        return await gateway.create_leadgen_form(page_id, await gateway.get_page_token(page_id), params,
                                                 idempotency_key=key)

    @staticmethod
    def _is_ours(row: LaunchStep, found: dict[str, Any]) -> bool:
        if not row.data.get("idempotency_key"):
            return False
        return created_after(row.data.get("key_at"), found.get("created_time"))

    async def _ensure(self, run: Run, step: str, *, kind: str, parent: str | None, lookup: tuple[str, str] | None,
                      method: str, make_params: Callable[[], dict[str, Any]], action: CreateObject | None,
                      budget: Money | None = None, extra: dict[str, Any] | None = None) -> str | None:
        """Один создающий шаг: реестр → поиск по тегу → ключ до вызова → создание → реестр."""
        row = await self.io(self._row, run.launch.id, step)
        if row.status == "done" and row.meta_id:
            return row.meta_id
        try:
            found = await self._find_existing(run, step, lookup)
            if found is None and run.create:
                found = await self._create(run, row, step, kind, method, make_params, action, lookup, extra)
        except MetaError as exc:
            await self.io(self._mark_failed, run.launch.id, step, exc.user_message_ru)
            raise StepFailed(step, exc.user_message_ru, exc.fbtrace_id) from exc
        except (PolicyDenied, StepFailed, ApprovalRequired):
            raise
        except Exception as exc:  # сбой не из Meta: запуск не теряем, останавливаем на шаге
            log.exception("Шаг %s прервался", step)
            await self.io(self._mark_failed, run.launch.id, step, str(exc))
            raise StepFailed(step, f"Внутренняя ошибка: {exc}") from exc
        if found is None:
            return None
        meta_id = str(found["id"])
        name = str(found.get("name") or run.tag(step))
        await self.io(self.register, run.company.id, run.launch.id, meta_id, kind, parent, name, budget,
                      str(found.get("status") or "PAUSED"))
        latest = await self.io(self._row, run.launch.id, step)  # со свежим ключом идемпотентности
        await self.io(self._save_row, latest, status="done", meta_id=meta_id, data={**latest.data, **(extra or {})})
        return meta_id

    async def _create(self, run: Run, row: LaunchStep, step: str, kind: str, method: str,
                      make_params: Callable[[], dict[str, Any]], action: CreateObject | None,
                      lookup: tuple[str, str] | None, extra: dict[str, Any] | None) -> dict[str, Any]:
        if action is not None:
            await self.guard(action, run.company.id, actor=run.actor)
        params = make_params()
        key = row.data.get("idempotency_key") or str(uuid.uuid4())
        key_at = row.data.get("key_at") or self._clock.now().isoformat()
        row = await self.io(self._save_row, row, status="running",
                            data={**row.data, **(extra or {}), "idempotency_key": key, "key_at": key_at})  # ДО вызова
        gateway = getattr(self.gateway(), method)
        call = (lambda: self._create_form(run, params, key)) if method == "create_leadgen_form" \
            else (lambda: gateway(run.account, params, idempotency_key=key))
        for attempt in range(CREATE_RETRIES):
            try:
                created = await self.mutate(run.actor, run.company.id, f"create:{step}",
                                            {"step": step, "launch_id": run.launch.id}, call)
                return {"id": created, "name": params.get("name"), "status": "PAUSED"}
            except MetaError as exc:
                if not exc.outcome_unknown or attempt == CREATE_RETRIES - 1:
                    raise
                found = await self._find_existing(run, step, lookup)  # могло создаться: сверка по тегу
                if found is not None:
                    return found
        raise StepFailed(step, "Не удалось создать объект.")

    async def _retire_guarded(self, run: Run) -> None:
        """Не удалось снять старую кампанию (ошибка или неизвестный исход) — ничего нового не создаём."""
        try:
            await self._retire_previous(run)
        except MetaError as exc:
            raise StepFailed("retire_previous", "Не удалось поставить на паузу предыдущую кампанию: "
                             f"{exc.user_message_ru} Новое не создавалось, чтобы не тратить две кампании сразу.",
                             exc.fbtrace_id) from exc

    def _previous_campaigns(self, company_id: int, current_launch: int | None) -> list:
        """Все прежние свои кампании компании (любой статус в реестре, кроме удалённых и архивных)."""
        return [o for o in self._db.objects.list(company_id=company_id, kind="campaign")
                if o.launch_id != current_launch and o.status not in DEAD_STATUSES]

    async def _require_previous_paused(self, run: Run) -> None:
        """Перед включением новой: каждая прежняя кампания по реестру И по факту в Meta не тратит."""
        previous = await self.io(self._previous_campaigns, run.company.id, run.launch.id)
        spending = [c.meta_id for c in previous if c.status == "ACTIVE"]
        for campaign in previous:
            try:
                state = await self.gateway().get_ad_status(campaign.meta_id)
            except MetaError as exc:
                raise StepFailed("activate", "Не удалось проверить, что предыдущая кампания на паузе "
                                 f"({campaign.meta_id}): {exc.user_message_ru} Новую не включаю.",
                                 exc.fbtrace_id) from exc
            if "ACTIVE" in (state.get("status"), state.get("effective_status")) and campaign.meta_id not in spending:
                spending.append(campaign.meta_id)
        if spending:
            raise StepFailed("activate", f"Предыдущая кампания ({', '.join(spending)}) не на паузе: новую не включаю, "
                             "чтобы не тратить две кампании сразу. Всё новое остаётся на паузе.")

    async def _retire_previous(self, run: Run) -> None:
        row = await self.io(self._row, run.launch.id, "retire_previous")
        if row.status == "done" or not run.create:
            return
        prev = await self.io(self._previous_campaigns, run.company.id, run.launch.id)
        retired = []
        for campaign in prev:
            retired.append(campaign.meta_id)
            # пауза в Meta ставится всем прежним: в реестре «на паузе» могло устареть (включили вручную)
            await self.guard(Pause(campaign.meta_id), run.company.id, actor=run.actor)
            await self.mutate(run.actor, run.company.id, "retire:pause", {"campaign": campaign.meta_id},
                              lambda c=campaign: self.gateway().set_status(c.meta_id, "PAUSED"))
            await self.io(self.set_registry, campaign.meta_id, status="PAUSED")
            await self._lower_cap(run, campaign.meta_id)
            await self.io(self.set_registry, campaign.meta_id, status=RETIRED)  # снятая никогда не включается
        await self.io(self._save_row, row, status="done", data={"retired": retired})

    async def _lower_cap(self, run: Run, campaign_id: str) -> None:
        """Защитный лимит предыдущей кампании опускается до потраченного (но не ниже минимума Meta)."""
        snapshot = await self.snapshot(run.company.id)
        state = snapshot.campaign(campaign_id)
        if state is None:
            return
        target = state.spent
        if snapshot.min_spend_cap is not None and target < snapshot.min_spend_cap:
            target = snapshot.min_spend_cap
        if state.spend_cap is not None and state.spend_cap <= target:
            return
        await self.guard(SetSpendCap(campaign_id, target), run.company.id, actor=run.actor, self_approve=True)
        await self.mutate(run.actor, run.company.id, "retire:spend_cap", {"campaign": campaign_id},
                          lambda: self.gateway().set_spend_cap(campaign_id, target))
        await self.io(self.save_cap, campaign_id, target)

    async def _step_campaign(self, run: Run) -> str | None:
        snapshot = await self.snapshot(run.company.id)
        cap = max(snapshot.remaining, snapshot.min_spend_cap or snapshot.remaining, key=lambda m: m.amount_minor)
        row = await self.io(self._row, run.launch.id, "campaign")
        stored = row.data.get("spend_cap")
        if stored:
            cap = Money(int(stored["amount_minor"]), stored["currency"])
        countries = tuple(sorted({c for g in run.spec.groups for c in g.countries}))
        name = run.name(run.spec.name, "campaign")
        campaign_id = await self._ensure(
            run, "campaign", kind="campaign", parent=None, lookup=(run.account, "campaigns"), method="create_campaign",
            make_params=lambda: campaign_params(run.spec, name, cap, countries), action=CreateObject("campaign"),
            extra={"spend_cap": money_json(cap)})
        if campaign_id:
            run.ids["campaign"] = campaign_id
        return campaign_id

    async def _step_audiences(self, run: Run) -> None:
        for n, group in enumerate(run.spec.groups, 1):
            if not group.needs_audience:
                continue
            step = f"audience:{n}"
            own = run.ids.get("retargeting") if group.hypothesis == "lookalike" else None
            if group.hypothesis == "lookalike" and not own:
                raise StepFailed(step, "Для похожей аудитории нет ретаргетинг-аудитории в этом запуске.")
            ig = run.company.ig_user_id or ""
            params = self._audience_params(group.hypothesis, run.name(f"{run.spec.name} {group.name}", step), ig, own)
            found = await self._ensure(run, step, kind="audience", parent=None, lookup=None,
                                       method="create_audience", make_params=lambda p=params: p,
                                       action=CreateObject("audience"))
            if found:
                run.ids[step] = found
                if group.hypothesis == "retargeting":
                    run.ids["retargeting"] = found

    @staticmethod
    def _audience_params(hypothesis: str, name: str, ig_user_id: str, origin: str | None) -> dict[str, Any]:
        if hypothesis == "lookalike":
            return {"name": name, "subtype": "LOOKALIKE", "origin_audience_id": origin,
                    "lookalike_spec": {"type": "similarity"}}
        rule = {"inclusions": {"operator": "or", "rules": [{"event_sources": [{"id": ig_user_id, "type": "ig_business"}],
                                                            "retention_seconds": RETENTION_SECONDS_YEAR,
                                                            "filter": {"operator": "and", "filters": [
                                                                {"field": "event", "operator": "eq",
                                                                 "value": "ig_business_profile_all"}]}}]}}
        return {"name": name, "subtype": "ENGAGEMENT", "prefill": 1, "rule": rule}

    async def _step_adsets(self, run: Run) -> None:
        audiences = {"retargeting": run.ids.get("retargeting", "")}
        for n, group in enumerate(run.spec.groups, 1):
            step = f"adset:{n}"
            own = run.ids.get(f"audience:{n}", "")
            targeting = targeting_dict(run.spec, group, audiences={**audiences, "own": own})
            name = run.name(f"{run.spec.name} · {group.name}", step)
            positions = run.spec.meta.positions
            action = CreateObject("group", run.ids["campaign"], group.daily_budget, group.goal_kind,  # type: ignore[arg-type]
                                  Targeting.instagram_only(positions))
            found = await self._ensure(
                run, step, kind="adset", parent=run.ids["campaign"], lookup=(run.ids["campaign"], "adsets"),
                method="create_adset", budget=group.daily_budget, action=action, extra={"goal_kind": group.goal_kind},
                make_params=lambda g=group, t=targeting, nm=name: adset_params(
                    run.spec, g, name=nm, campaign_id=run.ids["campaign"], page_id=run.company.page_id, targeting=t))
            if found:
                run.ids[step] = found

    async def _step_media(self, run: Run) -> None:
        for media_id in sorted({a.creative_id for g in run.spec.groups for a in g.ads if a.kind == "new_creative"}):
            step = f"media:{media_id}"
            row = await self.io(self._row, run.launch.id, step)
            if row.status == "done" and row.meta_id:
                run.ids[step] = row.meta_id
            elif run.create:
                asset = await self.io(self._db.media.get, int(media_id))
                if asset is None or not asset.path:
                    raise StepFailed(step, f"Файл креатива {media_id} не найден.")
                run.ids[step] = await self._upload(run, row, step, asset)

    async def _upload(self, run: Run, row: LaunchStep, step: str, asset) -> str:
        data = await self.io(lambda: open(asset.path, "rb").read())
        gateway = self.gateway()
        method = gateway.upload_video if asset.kind == "video" else gateway.upload_image
        try:
            await self.guard(CreateObject("creative"), run.company.id, actor=run.actor)
            meta_id = await self.mutate(run.actor, run.company.id, f"upload:{step}", {"step": step},
                                        lambda: method(run.account, data, asset.path.rsplit("/", 1)[-1]))
        except MetaError as exc:
            raise StepFailed(step, exc.user_message_ru, exc.fbtrace_id) from exc
        await self.io(self._save_row, row, status="done", meta_id=meta_id)
        return meta_id

    async def _step_leadform(self, run: Run) -> None:
        if not run.spec.needs_lead_form:
            return
        name = run.name(f"{run.spec.name} · форма", "leadform")
        found = await self._ensure(
            run, "leadform", kind="leadform", parent=None, lookup=(run.company.page_id or "", "leadgen_forms"),
            method="create_leadgen_form", action=CreateObject("creative"),  # форма - как креатив: денег не тратит
            make_params=lambda: leadform_params(run.spec, name))
        if found:
            run.ids["leadform"] = found

    def creative_params_for(self, run: Run, group_n: int, ad_m: int, name: str) -> dict[str, Any]:
        ad = run.spec.groups[group_n - 1].ads[ad_m - 1]
        image = run.ids.get(f"media:{ad.creative_id}")
        spec = replace(run.spec, lead_form_id=run.ids.get("leadform") or run.spec.lead_form_id)
        return creative_params(spec, ad, name=name, page_id=run.company.page_id or "",
                               ig_user_id=run.company.ig_user_id or "", image_hash=image)

    async def _step_creatives(self, run: Run) -> None:
        for n, group in enumerate(run.spec.groups, 1):
            for m in range(1, len(group.ads) + 1):
                step = f"creative:{n}:{m}"
                name = run.name(f"{run.spec.name} · {group.name} · {m}", step)
                found = await self._ensure(
                    run, step, kind="creative", parent=None, lookup=(run.account, "adcreatives"),
                    method="create_creative", action=CreateObject("creative"),
                    make_params=lambda n=n, m=m, nm=name: self.creative_params_for(run, n, m, nm),
                    extra={"params": self.creative_params_for(run, n, m, name)})
                if found:
                    run.ids[step] = found

    async def _step_ads(self, run: Run) -> None:
        for n, group in enumerate(run.spec.groups, 1):
            for m in range(1, len(group.ads) + 1):
                step, parent = f"ad:{n}:{m}", run.ids.get(f"adset:{n}")
                creative = run.ids.get(f"creative:{n}:{m}")
                if not parent or (not creative and not run.create):
                    continue
                name = run.name(f"{run.spec.name} · {group.name} · {m}", step)
                found = await self._ensure(
                    run, step, kind="ad", parent=parent, lookup=(parent, "ads"), method="create_ad",
                    action=CreateObject("ad", parent),
                    make_params=lambda p=parent, c=creative, nm=name: {
                        "name": nm, "adset_id": p, "creative": {"creative_id": c}, "status": "PAUSED"})
                if found:
                    run.ids[step] = found

    async def _step_activate(self, run: Run) -> None:
        row = await self.io(self._row, run.launch.id, "activate")
        if row.status == "done":
            return
        await self._require_previous_paused(run)
        order = [("ad", run.ids[k]) for k in sorted(run.ids) if k.startswith("ad:")]
        order += [("group", run.ids[k]) for k in sorted(run.ids) if k.startswith("adset:")]
        order.append(("campaign", run.ids["campaign"]))
        try:
            for kind, meta_id in order:  # кампания — последней
                await self._enable(run, kind, meta_id)
        except (StepFailed, PolicyDenied, ApprovalRequired):
            run.unpaused = await self._pause_launch_objects(run.launch, everything=True)  # при ЛЮБОЙ ошибке: всё дерево
            raise
        await self.io(self._save_row, row, status="done", data={"activated": [i for _, i in order]})

    async def _enable(self, run: Run, kind: str, meta_id: str) -> None:
        try:
            await self.guard(Activate(meta_id, kind), run.company.id, actor=run.actor, self_approve=True)  # type: ignore[arg-type]
            await self.enable_object(run.actor, run.company.id, meta_id, "activate", {"object_kind": kind})
        except MetaError as exc:
            raise StepFailed("activate", exc.user_message_ru, exc.fbtrace_id) from exc
        except (PolicyDenied, ApprovalRequired):
            raise
        except Exception as exc:
            raise StepFailed("activate", f"Внутренняя ошибка: {exc}") from exc

    # ------------------------------------------------------------------ пауза и откат
    async def _pause_launch_objects(self, launch: LaunchRow, *, everything: bool = False) -> list[str]:
        """Пауза объектов запуска (`everything` - не верить реестру). Возвращает то, что остановить не удалось."""
        objects = await self.io(lambda: self._db.objects.list(launch_id=launch.id))
        wanted = (lambda o: o.status not in DEAD_STATUSES) if everything else (lambda o: o.status == "ACTIVE")
        targets = sorted((o for o in objects if wanted(o) and o.kind in PAUSABLE), key=lambda o: o.kind != "campaign")
        failed: list[str] = []
        for obj in targets:
            if not await self._pause_with_retry(launch, obj.meta_id):
                failed.append(obj.meta_id)
        return failed

    async def _pause_with_retry(self, launch: LaunchRow, meta_id: str) -> bool:
        for _attempt in range(PAUSE_ATTEMPTS):
            try:
                await self.guard(Pause(meta_id), launch.company_id, actor="system", safety=True)
                await self.mutate("system", launch.company_id, "pause", {"object": meta_id},
                                  lambda: self.gateway().set_status(meta_id, "PAUSED"))
                await self.io(self.set_registry, meta_id, status="PAUSED")
                return True
            except Exception:
                log.exception("Не удалось поставить на паузу %s", meta_id)
        return False

    async def _delete_all(self, run: Run) -> list[str]:
        objects = await self.io(lambda: [o for o in self._db.objects.list(launch_id=run.launch.id)
                                         if o.status not in ("DELETED", "ARCHIVED")])
        left: list[str] = []
        for kind in KILL_ORDER:
            for obj in (o for o in objects if o.kind == kind):
                if not await self._remove(run, obj.meta_id):
                    left.append(obj.meta_id)
        return left

    async def _remove(self, run: Run, meta_id: str) -> bool:
        cid = run.company.id
        try:
            await self.guard(Delete(meta_id), cid, actor=run.actor)
            await self.mutate(run.actor, cid, "delete", {"object": meta_id}, lambda: self.gateway().delete(meta_id))
            await self.io(self.set_registry, meta_id, status="DELETED")
            return True
        except (MetaError, PolicyDenied):
            pass
        try:  # удалить нельзя — архив
            await self.mutate(run.actor, cid, "archive", {"object": meta_id},
                              lambda: self.gateway().set_status(meta_id, "ARCHIVED"))
            await self.io(self.set_registry, meta_id, status="ARCHIVED")
            return True
        except MetaError:
            return False

    async def _after_rollback(self, launch: LaunchRow) -> None:
        """Хук: после отката предыдущая кампания снова текущая (реализует Controls)."""

    async def _finish_rollback(self, launch: LaunchRow, plan: Plan, left: list[str]) -> LaunchResult:
        if left:
            text = f"Откат не завершён: не удалось убрать {', '.join(left)}. Они на паузе; повторите откат."
            await self.notify(text, [[(BUTTON_ROLLBACK, f"launch:rollback:{launch.id}")]])
            return LaunchResult(AWAITING_DECISION, text, plan.id, launch.id, created=tuple(left))
        await self._set_launch(launch, "rolled_back", None, finished=True)
        await self.io(self._db.plans.update, replace(plan, status="cancelled", updated_at=self._clock.now()))
        await self._after_rollback(launch)
        await self.audit("user", "launch:rollback", launch.company_id, "ok", launch_id=launch.id)
        return LaunchResult(ROLLED_BACK, "Запуск откатан: созданное удалено, деньги не потрачены.", plan.id, launch.id)
