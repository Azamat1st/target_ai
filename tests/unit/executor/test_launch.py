import asyncio
from datetime import timedelta

import pytest

from target_ai.executor import PolicyDenied
from target_ai.executor.results import ALREADY_DONE, AWAITING_DECISION, LAUNCHED, NEEDS_APPROVAL, ROLLED_BACK


async def test_launch_builds_paused_tree_and_activates_campaign_last(env):
    company = env.company()
    plan = env.plan(company)
    ex = env.executor()

    result = await env.launch(ex, plan)

    assert result.status == LAUNCHED
    campaign, = env.live("campaign")
    assert campaign["status"] == "ACTIVE" and campaign["spend_cap"] == 100000  # остаток месяца
    assert len(env.live("adset")) == 2 and len(env.live("ad")) == 4 and len(env.live("creative")) == 4
    posts = [r for r in env.mutations() if r.status == 200]
    creates = [r.path.split("/")[-1] for r in posts if r.path.startswith("act_")]
    assert creates == ["campaigns", "adsets", "adsets", "adcreatives", "adcreatives", "adcreatives", "adcreatives",
                       "ads", "ads", "ads", "ads"]
    activations = [r for r in posts if r.body.get("status") == "ACTIVE"]
    assert activations[-1].path == campaign["id"] and len(activations) == 7
    assert all(r.body["status"] == "PAUSED" for r in posts if r.path.endswith(("campaigns", "adsets", "ads")))


async def test_launch_objects_carry_tag_idempotency_key_and_instagram_only_targeting(env):
    company = env.company()
    ex = env.executor()
    await env.launch(ex, env.plan(company))

    launch = env.db.launches.list()[0]
    steps = {s.step: s for s in env.db.launch_steps.list(launch_id=launch.id)}
    assert steps["campaign"].data["idempotency_key"]
    request = next(r for r in env.fake.requests if r.method == "POST" and r.path.endswith("/campaigns"))
    assert request.headers["x-client-idempotent-key"] == steps["campaign"].data["idempotency_key"]
    assert f"[ta:{env.db.settings.get('install_id')}:L{launch.id}:campaign]" in request.body["name"] and request.body["special_ad_categories"] == []
    adset = next(r for r in env.fake.requests if r.path.endswith("/adsets") and r.method == "POST").body
    t = adset["targeting"]
    assert t["publisher_platforms"] == ["instagram"] and t["instagram_positions"] == ["stream", "story"]
    assert t["targeting_automation"] == {"advantage_audience": 1} and "placement_soft_opt_out" not in t
    creative = next(r for r in env.fake.requests if r.method == "POST" and r.path.endswith("/adcreatives")).body
    enroll = creative["degrees_of_freedom_spec"]["creative_features_spec"]["standard_enhancements"]["enroll_status"]
    assert enroll == "OPT_OUT"


async def test_confirm_mode_launch_without_approval_creates_nothing(env):
    company = env.company()
    plan = env.plan(company)
    ex = env.executor()

    result = await ex.launch(plan.id, None)

    assert result.status == NEEDS_APPROVAL and result.needs_approval.key
    assert env.live("campaign") == [] and env.db.launches.list() == []


async def test_double_click_creates_exactly_one_campaign(env):
    company = env.company()
    plan = env.plan(company)
    ex = env.executor()
    check = await ex.check_launch(plan.id)
    approval = ex.create_approval(check.needs_approval, company_id=company.id).id

    first, second = await asyncio.gather(ex.launch(plan.id, approval), ex.launch(plan.id, approval))
    third = await ex.launch(plan.id, approval)

    assert sorted([first.status, second.status]) == [ALREADY_DONE, LAUNCHED]
    assert third.status == ALREADY_DONE
    assert len(env.live("campaign")) == 1 and len(env.live("ad")) == 4


async def test_failure_midway_pauses_everything_then_recovery_continues_without_duplicates(env):
    company = env.company()
    plan = env.plan(company)
    flaky = env.executor(env.flaky(fail_ad_at=3))  # ad:2:1

    result = await env.launch(flaky, plan)

    assert result.status == AWAITING_DECISION and result.failed_step == "ad:2:1"
    assert all(o["status"] == "PAUSED" for o in env.live("campaign") + env.live("adset") + env.live("ad"))
    env.clock.advance(timedelta(days=1))
    assert env.sim.simulate_day(env.clock.now().date()) == 0  # расхода нет
    fresh = env.executor()
    items = await fresh.recover_incomplete()
    assert len(items) == 1 and items[0].step_number == 11 and "Довести" in str(env.notifier.sent[-1][1])
    done = await fresh.continue_launch(items[0].launch_id)
    assert done.status == LAUNCHED
    assert len(env.live("campaign")) == 1 and len(env.live("ad")) == 4 and len(env.live("creative")) == 4


async def test_object_created_in_meta_but_unrecorded_is_found_by_tag_on_recovery(env):
    company = env.company()
    plan = env.plan(company)
    lossy = env.executor(env.flaky(lose_adset_response=True))

    result = await env.launch(lossy, plan)

    assert result.failed_step == "adset:1" and env.db.objects.get_by_meta_id(env.live("adset")[0]["id"]) is None
    items = await env.executor().recover_incomplete()
    assert env.live("adset")[0]["id"] in items[0].created
    done = await env.executor().continue_launch(items[0].launch_id)
    assert done.status == LAUNCHED and len(env.live("adset")) == 2


async def test_rollback_deletes_everything_created(env):
    company = env.company()
    plan = env.plan(company)
    result = await env.launch(env.executor(env.flaky(fail_ad_at=3)), plan)

    rolled = await env.executor().rollback_launch(result.launch_id)

    assert rolled.status == ROLLED_BACK
    assert env.live("campaign") == [] and env.live("adset") == [] and env.live("ad") == [] and env.live("creative") == []
    assert env.db.plans.get(plan.id).status == "cancelled"


async def test_new_launch_retires_previous_campaign_and_lowers_its_cap(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    first = await env.launch(ex, env.plan(company))
    second = await env.launch(ex, env.plan(company, version=2))

    old = env.fake.object(first.campaign_id)
    assert second.status == LAUNCHED and old["status"] == "PAUSED" and old["spend_cap"] == 10000  # минимум Meta
    assert sum(1 for c in env.live("campaign") if c["status"] == "ACTIVE") == 1


async def test_spend_cap_below_meta_minimum_raised_and_requires_button_even_in_autonomous(env):
    env.clock.advance(timedelta(days=21))  # конец месяца: остаток делится на 2 дня
    company = env.company(mode="autonomous", limit_major=80)
    plan = env.plan(company, budget=500)
    ex = env.executor()

    check = await ex.check_launch(plan.id)
    result = await ex.launch(plan.id, None)

    assert check.status == NEEDS_APPROVAL and result.status == NEEDS_APPROVAL
    done = await env.launch(ex, plan)
    assert done.status == LAUNCHED and env.live("campaign")[0]["spend_cap"] == 10000


async def test_halted_system_denies_launch(env):
    company = env.company(mode="autonomous")
    plan = env.plan(company)
    env.db.settings.set("halted", True)

    with pytest.raises(PolicyDenied) as err:
        await env.executor().launch(plan.id, None)

    assert err.value.code == "halted" and env.live("campaign") == []


async def test_every_meta_change_is_in_audit_log_with_actor_and_company(env):
    company = env.company()
    await env.launch(env.executor(), env.plan(company))

    rows = env.db.audit.list()
    creates = [r for r in rows if r.action == "create:campaign"]
    assert {r.data["outcome"] for r in creates} == {"intent", "ok"}
    assert all(r.actor == "user" and r.company_id == company.id for r in creates)
    assert any(r.action == "activate" for r in rows)


async def test_lost_response_on_create_is_resolved_by_tag_without_duplicate(env):
    from target_ai.fakemeta.errors import FakeError

    company = env.company()
    plan = env.plan(company)
    env.fake.inject_error(r"POST act_\d+/campaigns", FakeError(status=500, code=1, message="сбой", after_execute=True))

    result = await env.launch(env.executor(), plan)

    assert result.status == LAUNCHED and len(env.live("campaign")) == 1


# ---------------------------------------------------------------- новый план поверх работающей кампании
def _active_budgets(env):
    groups = {g["id"]: g for g in env.live("adset")}
    return sum(g["daily_budget"] for g in groups.values()
               if g["status"] == "ACTIVE" and env.fake.object(g["campaign_id"])["status"] == "ACTIVE")


@pytest.mark.parametrize("mode", ["confirm", "autonomous"])
async def test_new_plan_over_running_campaign_launches_with_one_active_campaign(env, mode):
    company = env.company(mode=mode)
    ex = env.executor()
    first = await env.launch(ex, env.plan(company, budget=2000))  # 2 x 2000: тратит почти весь дневной потолок
    plan = env.plan(company, version=2, budget=2000)
    snapshot = await ex.snapshot(company.id)

    second = await env.launch(ex, plan)  # без снятия старой сумма 8000 была бы выше потолка

    assert second.status == LAUNCHED
    assert env.fake.object(first.campaign_id)["status"] == "PAUSED"
    assert [c["id"] for c in env.live("campaign") if c["status"] == "ACTIVE"] == [second.campaign_id]
    assert _active_budgets(env) == 4000 <= snapshot.daily_cap.amount_minor


async def test_plan_above_cap_on_its_own_is_still_denied_over_running_campaign(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    first = await env.launch(ex, env.plan(company, budget=1000))
    huge = env.plan(company, version=2, budget=50000)

    with pytest.raises(PolicyDenied):
        await ex.check_launch(huge.id)
    with pytest.raises(PolicyDenied):
        await ex.launch(huge.id, None)

    assert env.fake.object(first.campaign_id)["status"] == "ACTIVE" and len(env.live("campaign")) == 1


async def test_failed_pause_of_previous_campaign_stops_saga_before_creating_anything(env):
    from target_ai.fakemeta import errors
    company = env.company(mode="autonomous")
    ex = env.executor()
    first = await env.launch(ex, env.plan(company, budget=1000))
    before = _active_budgets(env)
    env.fake.inject_error(rf"POST {first.campaign_id}$", errors.server_error(), times=20)
    mutations_before = len(env.mutations())
    plan = env.plan(company, version=2, budget=1000)

    result = await env.launch(ex, plan)

    assert result.status == AWAITING_DECISION and result.failed_step == "retire_previous"
    assert len(env.live("campaign")) == 1 and env.fake.object(first.campaign_id)["status"] == "ACTIVE"
    assert _active_budgets(env) == before and not env.db.objects.list(launch_id=result.launch_id)
    assert [r for r in env.mutations()[mutations_before:] if r.path.startswith("act_")] == []


async def test_activate_refuses_while_previous_campaign_is_still_active_in_registry(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    first = await env.launch(ex, env.plan(company, budget=1000))
    original = ex._retire_previous

    async def retire_but_leave_active(run):
        await original(run)
        await ex.io(ex.set_registry, first.campaign_id, status="ACTIVE")  # реестр говорит: старая снова тратит

    ex._retire_previous = retire_but_leave_active
    result = await env.launch(ex, env.plan(company, version=2, budget=1000))

    assert result.status == AWAITING_DECISION and result.failed_step == "activate"
    assert all(c["status"] == "PAUSED" for c in env.live("campaign") if c["id"] != first.campaign_id)


# ---------------------------------------------------------------- старая кампания включена вручную
def _turn_on_manually(env, campaign_id):
    env.fake.state.obj(campaign_id)["status"] = "ACTIVE"  # Ads Manager; реестр об этом не знает
    env.fake.state.save()


@pytest.mark.parametrize("mode", ["confirm", "autonomous"])
async def test_previous_campaign_switched_on_by_hand_is_paused_before_new_one_activates(env, mode):
    company = env.company(mode=mode)
    ex = env.executor()
    first = await env.launch(ex, env.plan(company, budget=2000))
    await ex.pause_company(company.id)
    _turn_on_manually(env, first.campaign_id)
    assert env.db.objects.get_by_meta_id(first.campaign_id).status != "ACTIVE"

    second = await env.launch(ex, env.plan(company, version=2, budget=2000))

    assert second.status == LAUNCHED
    assert [c["id"] for c in env.live("campaign") if c["status"] == "ACTIVE"] == [second.campaign_id]
    assert _active_budgets(env) == 4000


async def test_approval_issued_while_old_ran_then_old_switched_on_by_hand_does_not_double_spend(env):
    company = env.company(mode="confirm")
    ex = env.executor()
    first = await env.launch(ex, env.plan(company, budget=2000))
    plan = env.plan(company, version=2, budget=2000)
    check = await ex.check_launch(plan.id)
    approval = ex.create_approval(check.needs_approval, company_id=company.id).id
    await ex.pause_company(company.id)
    _turn_on_manually(env, first.campaign_id)

    result = await ex.launch(plan.id, approval)

    assert result.status == LAUNCHED
    assert [c["id"] for c in env.live("campaign") if c["status"] == "ACTIVE"] == [result.campaign_id]
    assert _active_budgets(env) == 4000


async def test_activate_refuses_when_meta_shows_previous_campaign_active_despite_paused_registry(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    first = await env.launch(ex, env.plan(company, budget=1000))
    original = ex._retire_previous

    async def retire_then_switch_on_by_hand(run):
        await original(run)
        _turn_on_manually(env, first.campaign_id)

    ex._retire_previous = retire_then_switch_on_by_hand
    result = await env.launch(ex, env.plan(company, version=2, budget=1000))

    assert result.status == AWAITING_DECISION and result.failed_step == "activate"
    assert [c["id"] for c in env.live("campaign") if c["status"] == "ACTIVE"] == [first.campaign_id]
