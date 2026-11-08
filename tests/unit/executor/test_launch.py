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
