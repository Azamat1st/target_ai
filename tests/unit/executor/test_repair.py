"""Атаки денежного ревью таска 05 (e1–e6) и ужесточение тестов; каждая красная на коде до ремонта."""

import asyncio
from datetime import timedelta

import pytest

from target_ai.executor import PolicyDenied
from target_ai.executor.results import AWAITING_DECISION, BUSY, LAUNCHED, ROLLED_BACK
from target_ai.fakemeta.errors import FakeError, server_error
from target_ai.meta.errors import MetaError, TRANSIENT
from target_ai.money import Money

TRACE = "TRACE-ACT-1"


class Crash(BaseException):
    """Обрыв процесса посреди запуска: не перехватывается ни одним `except Exception`."""


class ActivationBreaks:
    """Шлюз: n-е включение исполняется в Meta, а потом ответ теряется (ошибка или обрыв процесса)."""

    def __init__(self, inner, *, nth, crash=False):
        self._inner, self.nth, self.crash, self.count = inner, nth, crash, 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def set_status(self, object_id, status):
        await self._inner.set_status(object_id, status)
        if status != "ACTIVE":
            return
        self.count += 1
        if self.count == self.nth:
            if self.crash:
                raise Crash()
            raise MetaError(TRANSIENT, "сбой после исполнения", outcome_unknown=True, fbtrace_id=TRACE)


def break_activation(env, **kw):
    return env.executor(lambda client: ActivationBreaks(client, **kw))


def activations(env):
    return [r for r in env.mutations() if r.body.get("status") == "ACTIVE"]


# ---------------------------------------------------------------- пункт 1
async def test_halt_raised_between_check_and_meta_call_enables_nothing(env, monkeypatch):
    company = env.company(mode="autonomous")
    plan = env.plan(company)
    ex = env.executor()
    real = env.db.audit.create

    def create(entry):  # стоп-кран нажат после проверки, до вызова Meta
        if entry.action == "activate" and entry.data["outcome"] == "intent":
            env.db.settings.set("halted", True)
        return real(entry)

    monkeypatch.setattr(env.db.audit, "create", create)

    result = await env.launch(ex, plan)

    assert result.status == AWAITING_DECISION
    assert activations(env) == []
    assert env.live("campaign")[0]["status"] == "PAUSED"
    assert env.db.launches.list()[0].status == AWAITING_DECISION


# ---------------------------------------------------------------- пункт 2
async def test_activation_error_awaits_decision_pauses_unknown_object_and_logs_fbtrace(env):
    company = env.company(mode="autonomous")
    plan = env.plan(company)
    ex = break_activation(env, nth=7)  # включение кампании: исполнилось, ответ потерян

    result = await env.launch(ex, plan)

    assert result.status == AWAITING_DECISION and result.failed_step == "activate"
    assert env.db.launches.list()[0].status == AWAITING_DECISION
    assert env.live("campaign")[0]["status"] == "PAUSED"
    assert env.db.objects.get_by_meta_id(env.live("campaign")[0]["id"]).status == "PAUSED"
    errors = [r for r in env.db.audit.list() if r.action == "activate" and r.data["outcome"] == "error"]
    assert errors and errors[0].data["fbtrace_id"] == TRACE
    assert [b for row in env.notifier.sent[-1][1] for b, _ in row] == ["Повторить", "Откатить"]


# ---------------------------------------------------------------- пункт 3
async def test_halt_all_pauses_campaign_even_if_registry_says_paused_and_counts_real_stops(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    result = await env.launch(ex, env.plan(company))
    env.db.objects.update(_replace_status(env.db.objects.get_by_meta_id(result.campaign_id), "PAUSED"))
    assert env.fake.object(result.campaign_id)["status"] == "ACTIVE"

    report = await ex.halt_all()

    assert env.fake.object(result.campaign_id)["status"] == "PAUSED"
    assert report.paused == (result.campaign_id,) and not report.failed


def _replace_status(obj, status):
    from dataclasses import replace
    return replace(obj, status=status)


async def test_halt_all_does_not_count_a_campaign_whose_pause_did_not_happen(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    result = await env.launch(ex, env.plan(company))
    env.fake.inject_error(rf"POST {result.campaign_id}$", server_error(), times=10)

    report = await ex.halt_all()

    assert report.paused == () and report.failed[0][0] == result.campaign_id


# ---------------------------------------------------------------- пункт 4
async def test_foreign_object_with_our_tag_is_not_adopted(env):
    company = env.company(mode="autonomous")
    env.db.settings.set("install_id", "abc12345")
    foreign = env.fake.state.add_object(
        "campaign", "act_100000000000001", {"name": "Копия клиента [ta:abc12345:L1:campaign]", "objective": "OUTCOME_ENGAGEMENT"},
        status="PAUSED", foreign=True)
    plan = env.plan(company)

    result = await env.launch(env.executor(), plan)

    assert result.status == AWAITING_DECISION and "не системой" in result.message_ru
    assert env.db.objects.get_by_meta_id(foreign["id"]) is None
    assert not [r for r in env.mutations() if r.path.split("/")[0] == foreign["id"]]
    assert env.live("adset") == []


async def test_object_with_our_tag_older_than_our_key_is_not_adopted(env):
    company = env.company(mode="autonomous")
    plan = env.plan(company)
    first = await env.launch(env.executor(env.flaky(lose_adset_response=True)), plan)
    adset = env.live("adset")[0]["id"]
    env.fake.object(adset)["created_time"] = "2026-01-01T00:00:00+00:00"
    assert env.fake.object(adset)["created_time"].startswith("2026-01-01")

    outcome = await env.executor().continue_launch(first.launch_id)

    assert outcome.status == AWAITING_DECISION and "не системой" in outcome.message_ru
    assert env.db.objects.get_by_meta_id(adset) is None
    assert not [r for r in env.mutations() if r.path.split("/")[0] == adset]


# ---------------------------------------------------------------- пункт 5
async def test_two_plans_of_one_company_launch_strictly_one_at_a_time(env):
    company = env.company(mode="autonomous")
    first, second = env.plan(company), env.plan(company, version=2)
    ex = env.executor()

    a, b = await asyncio.gather(env.launch(ex, first), env.launch(ex, second))

    assert sorted([a.status, b.status]) == [BUSY, LAUNCHED]
    assert "дождитесь" in next(x for x in (a, b) if x.status == BUSY).message_ru
    active = [c for c in env.live("campaign") if c["status"] == "ACTIVE"]
    assert len(active) == 1 and len(env.live("campaign")) == 1


async def test_launch_is_refused_while_another_launch_of_the_company_awaits_decision(env):
    company = env.company(mode="autonomous")
    stuck = await env.launch(env.executor(env.flaky(fail_ad_at=3)), env.plan(company))

    other = await env.launch(env.executor(), env.plan(company, version=2))

    assert stuck.status == AWAITING_DECISION and other.status == BUSY
    assert len(env.live("campaign")) == 1


# ---------------------------------------------------------------- пункт 6
async def test_resume_company_enables_only_the_current_campaign_never_a_retired_one(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    old = await env.launch(ex, env.plan(company))
    new = await env.launch(ex, env.plan(company, version=2))
    await ex.pause_company(company.id)
    before = len(activations(env))

    resumed = await ex.resume_company(company.id)

    assert resumed.status == "done"
    assert env.fake.object(new.campaign_id)["status"] == "ACTIVE"
    assert env.fake.object(old.campaign_id)["status"] == "PAUSED"
    assert env.db.objects.get_by_meta_id(old.campaign_id).status == "RETIRED"
    assert not [r for r in activations(env)[before:] if r.path == old.campaign_id]


async def test_resume_company_refuses_when_only_retired_campaigns_remain_paused(env):
    from target_ai.executor import ExecutorError

    company = env.company(mode="autonomous")
    ex = env.executor()
    await env.launch(ex, env.plan(company))
    await env.launch(ex, env.plan(company, version=2))
    await ex.halt_all()
    approval = env.db.approvals.create(kind="resume_system", now=env.clock.now())
    await ex.resume_system(approval.id)
    env.db.objects.update(_replace_status(env.db.objects.list(company_id=company.id, kind="campaign")[-1], "RETIRED"))

    with pytest.raises(ExecutorError):
        await ex.resume_company(company.id)
    assert [c for c in env.live("campaign") if c["status"] == "ACTIVE"] == []


# ---------------------------------------------------------------- пункт 7
@pytest.mark.parametrize("mode", ["autonomous", "confirm"])
async def test_lowering_monthly_limit_after_second_launch_is_immediate_and_caps_both_campaigns(env, mode):
    company = env.company(mode=mode)
    ex = env.executor()
    old = await env.launch(ex, env.plan(company))
    new = await env.launch(ex, env.plan(company, version=2))

    outcome = await ex.set_monthly_limit(company.id, Money(30000, "USD"))

    assert outcome.status == "done"
    assert env.db.companies.get(company.id).monthly_limit == Money(30000, "USD")
    assert env.fake.object(new.campaign_id)["spend_cap"] == 30000
    assert env.fake.object(old.campaign_id)["spend_cap"] == 10000  # потрачено 0, минимум Meta


async def test_failed_meta_cap_lowering_keeps_old_limit_and_reports(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    result = await env.launch(ex, env.plan(company))
    env.fake.inject_error(rf"POST {result.campaign_id}$", server_error(), times=10)

    outcome = await ex.set_monthly_limit(company.id, Money(50000, "USD"))

    assert outcome.status == "failed" and outcome.message_ru
    assert env.db.companies.get(company.id).monthly_limit == Money(100000, "USD")


async def test_month_rollover_in_autonomous_raises_cap_within_limit_without_button(env):
    from dataclasses import replace

    company = env.company(mode="autonomous")
    ex = env.executor()
    result = await env.launch(ex, env.plan(company))
    await ex.set_monthly_limit(company.id, Money(50000, "USD"))
    assert env.fake.object(result.campaign_id)["spend_cap"] == 50000
    env.db.companies.update(replace(env.db.companies.get(company.id), monthly_limit=Money(80000, "USD")))

    outcome = await ex.month_rollover(company.id)

    assert outcome.status == "done" and env.fake.object(result.campaign_id)["spend_cap"] == 80000


# ---------------------------------------------------------------- пункт 8
async def test_launch_is_rechecked_after_approval_is_consumed(env, monkeypatch):
    from dataclasses import replace

    company = env.company(mode="autonomous")
    plan = env.plan(company, budget=2000)
    ex = env.executor()
    begin = ex._begin

    def squeezed(*args):  # лимит срезали между проверкой и вставкой запуска
        env.db.companies.update(replace(env.db.companies.get(company.id), monthly_limit=Money(3000, "USD")))
        return begin(*args)

    monkeypatch.setattr(ex, "_begin", squeezed)

    result = await ex.launch(plan.id, None)

    assert result.status == ROLLED_BACK
    assert env.live("campaign") == [] and not [r for r in env.mutations() if r.status == 200]


# ---------------------------------------------------------------- пункт 9
async def test_test_level_cleanup_removes_parent_created_but_never_confirmed(env):
    class LosesParent:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def create_campaign(self, account_id, params, *, idempotency_key=None):
            await self._inner.create_campaign(account_id, params, idempotency_key=idempotency_key)
            raise RuntimeError("ответ потерян")

    company = env.company()
    plan = env.plan(company)
    env.db.settings.set("level", "test")
    ex = env.executor(LosesParent)

    with pytest.raises(RuntimeError):
        await ex.launch(plan.id, None)

    assert env.live("campaign") == []


async def test_test_level_reports_leftover_that_cannot_be_removed(env):
    company = env.company()
    plan = env.plan(company)
    env.db.settings.set("level", "test")
    env.fake.inject_error(r"DELETE \d+$", server_error(), times=50)

    result = await env.executor().launch(plan.id, None)

    assert result.status == "validation_failed" and result.created
    assert env.db.plans.get(plan.id).status != "validated"


# ---------------------------------------------------------------- пункт 10
async def test_lost_response_is_resolved_by_tag_when_fake_ignores_idempotency_keys(env):
    company = env.company()
    env.fake.ignore_idempotency_keys = True
    env.fake.inject_error(r"POST act_\d+/campaigns", FakeError(status=500, code=1, message="сбой", after_execute=True))

    result = await env.launch(env.executor(), env.plan(company))

    assert result.status == LAUNCHED and len(env.live("campaign")) == 1


async def test_after_halt_spend_cap_growth_is_refused(env):
    from dataclasses import replace

    company = env.company(mode="autonomous")
    ex = env.executor()
    result = await env.launch(ex, env.plan(company))
    await ex.halt_all()
    env.db.companies.update(replace(env.db.companies.get(company.id), monthly_limit=Money(200000, "USD")))

    with pytest.raises(PolicyDenied) as rollover:
        await ex.month_rollover(company.id)
    with pytest.raises(PolicyDenied) as up:
        await ex.set_monthly_limit(company.id, Money(300000, "USD"))

    assert rollover.value.code == "halted" and up.value.code == "halted"
    assert env.fake.object(result.campaign_id)["spend_cap"] == 100000


async def test_halt_flag_is_written_before_the_first_mutating_request(env):
    seen = []

    class Spy:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def set_status(self, object_id, status):
            if status == "PAUSED":
                seen.append(env.db.settings.get("halted"))
            return await self._inner.set_status(object_id, status)

    company = env.company(mode="autonomous")
    plain = env.executor()
    await env.launch(plain, env.plan(company))
    seen.clear()

    await env.executor(Spy).halt_all()

    assert seen and all(flag is True for flag in seen)


async def test_recover_incomplete_pauses_object_active_in_meta_of_interrupted_launch(env):
    company = env.company(mode="autonomous")
    plan = env.plan(company)
    with pytest.raises(Crash):
        await env.launch(break_activation(env, nth=7, crash=True), plan)  # кампания включилась, процесс упал
    assert env.live("campaign")[0]["status"] == "ACTIVE"

    items = await env.executor().recover_incomplete()

    assert len(items) == 1
    assert all(o["status"] == "PAUSED" for o in env.live("campaign") + env.live("adset") + env.live("ad"))


# ================================================================ дозапрос 2 (e7)
class FailsAfterExecuting:
    """Meta исполняет включение n-го объекта, но отвечает 4xx (не «неизвестный исход»)."""

    def __init__(self, inner, *, nth):
        self._inner, self.nth, self.count = inner, nth, 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def set_status(self, object_id, status):
        await self._inner.set_status(object_id, status)
        if status == "ACTIVE":
            self.count += 1
            if self.count == self.nth:
                raise MetaError("invalid_parameter", "отклонено после исполнения", http_status=400)


async def test_any_activation_error_pauses_whole_tree_before_saying_everything_is_paused(env):
    company = env.company(mode="autonomous")
    ex = env.executor(lambda client: FailsAfterExecuting(client, nth=7))

    result = await env.launch(ex, env.plan(company))

    assert result.status == AWAITING_DECISION
    everything = env.live("campaign") + env.live("adset") + env.live("ad")
    assert everything and all(o["status"] == "PAUSED" for o in everything)
    assert all(o.status != "ACTIVE" for o in env.db.objects.list(company_id=company.id))
    assert "на паузе" in result.message_ru and "не удалось" not in result.message_ru.lower()


async def test_failed_pause_of_tree_is_reported_truthfully(env):
    company = env.company(mode="autonomous")

    class StubbornAds(FailsAfterExecuting):
        async def set_status(self, object_id, status):
            if status == "PAUSED" and self.count >= self.nth:
                raise MetaError("transient", "пауза не прошла", outcome_unknown=True)
            return await super().set_status(object_id, status)

    ex = env.executor(lambda client: StubbornAds(client, nth=3))
    result = await env.launch(ex, env.plan(company))

    assert result.status == AWAITING_DECISION and "не удалось" in result.message_ru.lower()


async def test_halt_raised_during_resume_request_pauses_the_campaign_again(env):
    class HaltsDuring:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def set_status(self, object_id, status):
            await self._inner.set_status(object_id, status)
            if status == "ACTIVE":
                env.db.settings.set("halted", True)

    company = env.company(mode="autonomous")
    plain = env.executor()
    result = await env.launch(plain, env.plan(company))
    await plain.pause_company(company.id)

    with pytest.raises(PolicyDenied) as denied:
        await env.executor(HaltsDuring).resume_company(company.id)

    assert denied.value.code == "halted"
    assert env.fake.object(result.campaign_id)["status"] == "PAUSED"
    assert env.db.objects.get_by_meta_id(result.campaign_id).status == "PAUSED"


async def test_halt_raised_before_resume_request_sends_no_enabling_call(env, monkeypatch):
    company = env.company(mode="autonomous")
    ex = env.executor()
    result = await env.launch(ex, env.plan(company))
    await ex.pause_company(company.id)
    real = env.db.audit.create

    def create(entry):
        if entry.action == "resume" and entry.data["outcome"] == "intent":
            env.db.settings.set("halted", True)
        return real(entry)

    monkeypatch.setattr(env.db.audit, "create", create)
    before = len(activations(env))

    with pytest.raises(PolicyDenied):
        await ex.resume_company(company.id)

    assert len(activations(env)) == before and env.fake.object(result.campaign_id)["status"] == "PAUSED"


async def test_object_of_another_installation_with_same_launch_numbers_is_never_adopted(env):
    company = env.company(mode="autonomous")
    plan = env.plan(company)
    env.fake.inject_error(r"POST act_\d+/campaigns", FakeError(status=400, code=100, message="отклонено"))
    stuck = await env.launch(env.executor(), plan)
    assert stuck.status == AWAITING_DECISION
    account = "act_100000000000001"
    old_format = env.fake.state.add_object("campaign", account, {"name": "Чужая [ta:L1:campaign]"},
                                           status="PAUSED", foreign=True)
    other = env.fake.state.add_object("campaign", account, {"name": "Чужая [ta:deadbeef:L1:campaign]"},
                                      status="PAUSED", foreign=True)

    done = await env.executor().continue_launch(stuck.launch_id)

    assert done.status == LAUNCHED and done.campaign_id not in (old_format["id"], other["id"])
    assert not [r for r in env.mutations() if r.path.split("/")[0] in (old_format["id"], other["id"])]
    assert env.db.settings.get("install_id")


async def test_rollback_of_second_launch_makes_previous_campaign_current_and_resumable(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    first = await env.launch(ex, env.plan(company))
    stuck = await env.launch(env.executor(env.flaky(fail_ad_at=3)), env.plan(company, version=2))
    assert stuck.status == AWAITING_DECISION
    assert env.db.objects.get_by_meta_id(first.campaign_id).status == "RETIRED"
    assert env.fake.object(first.campaign_id)["spend_cap"] == 10000

    await env.executor().rollback_launch(stuck.launch_id)

    assert env.db.objects.get_by_meta_id(first.campaign_id).status == "PAUSED"
    assert env.fake.object(first.campaign_id)["spend_cap"] == 100000
    resumed = await env.executor().resume_company(company.id)
    assert resumed.status == "done" and env.fake.object(first.campaign_id)["status"] == "ACTIVE"
