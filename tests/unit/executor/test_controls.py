from dataclasses import replace

import pytest

from target_ai.agents.schemas import AdVariant
from target_ai.executor import ApprovalInvalid, PolicyDenied
from target_ai.executor.results import LAUNCHED, VALIDATED
from target_ai.fakemeta.errors import server_error
from target_ai.money import Money
from target_ai.policy import OptimizerDecision


async def launched(env, ex, company, **kw):
    return await env.launch(ex, env.plan(company, **kw))


async def test_foreign_campaign_gets_no_mutating_request_through_the_whole_cycle(env):
    company = env.company()
    ex = env.executor()
    result = await launched(env, ex, company)
    adset = env.live("adset")[0]["id"]
    await ex.apply_decisions(company.id, [OptimizerDecision("pause_group", adset, None, "слабая")])
    await ex.pause_company(company.id)
    await ex.halt_all()

    foreign = {o["id"] for o in env.fake.objects() if o.get("foreign")}
    assert foreign and all(o["status"] == "ACTIVE" for o in env.fake.objects() if o.get("foreign"))
    touched = {r.path.split("/")[0] for r in env.mutations()}
    assert not touched & foreign and result.status == LAUNCHED


async def test_halt_all_sets_flag_first_pauses_all_and_error_on_one_does_not_stop_others(env):
    first, second = env.company(name="A"), env.company("EUR", name="B")
    ex = env.executor()
    one = await launched(env, ex, first)
    two = await launched(env, ex, second)
    env.fake.inject_error(rf"POST {one.campaign_id}$", server_error(), times=5)

    report = await ex.halt_all()

    assert env.db.settings.get("halted") is True
    assert report.paused == (two.campaign_id,) and report.failed[0][0] == one.campaign_id
    assert env.fake.object(two.campaign_id)["status"] == "PAUSED"
    assert "остановлено 1" in report.message_ru and one.campaign_id in report.message_ru


async def test_after_halt_nothing_can_be_enabled_until_resume_system_which_enables_nothing(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    result = await launched(env, ex, company)
    group = env.live("adset")[0]["id"]
    env.db.settings.set("halted", True)  # флаг уже стоит, а группа ещё тратит
    report = await ex.apply_decisions(company.id, [OptimizerDecision("scale", group, Money(1200, "USD"), "рост")])
    assert report.applied[0].status == "denied"
    await ex.halt_all()
    with pytest.raises(PolicyDenied) as denied:
        await ex.resume_company(company.id)
    assert denied.value.code == "halted"

    approval = env.db.approvals.create(kind="resume_system", now=env.clock.now())
    outcome = await ex.resume_system(approval.id)
    with pytest.raises(ApprovalInvalid):
        await ex.resume_system(approval.id)

    assert outcome.status == "done" and env.db.settings.get("halted") is False
    assert env.fake.object(result.campaign_id)["status"] == "PAUSED"  # флаг снят, ничего не включено
    resumed = await ex.resume_company(company.id)
    assert resumed.status == "done" and env.fake.object(result.campaign_id)["status"] == "ACTIVE"


async def test_resume_company_in_confirm_mode_needs_button_and_approval_is_single_use(env):
    company = env.company()
    ex = env.executor()
    result = await launched(env, ex, company)
    await ex.pause_company(company.id)

    asked = await ex.resume_company(company.id)
    approval = ex.create_approval(asked.needs_approval, company_id=company.id)
    done = await ex.resume_company(company.id, approval.id)

    assert asked.status == "needs_approval" and done.status == "done"
    assert env.fake.object(result.campaign_id)["status"] == "ACTIVE"
    await ex.pause_company(company.id)
    again = await ex.resume_company(company.id, approval.id)
    assert again.status == "needs_approval"  # использованное подтверждение не действует


async def test_decision_batch_stops_when_a_pause_fails(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    await launched(env, ex, company)
    first, second = [a["id"] for a in env.live("adset")]
    env.fake.inject_error(rf"POST {first}$", server_error(), times=5)
    decisions = [OptimizerDecision("pause_group", first, None, "слабая"),
                 OptimizerDecision("pause_group", second, None, "слабая")]

    report = await ex.apply_decisions(company.id, decisions)

    assert report.stopped and [a.status for a in report.applied] == ["failed", "skipped"]
    assert env.fake.object(second)["status"] == "ACTIVE"


async def test_decisions_apply_and_update_registry_and_audit(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    await launched(env, ex, company)
    second = env.live("adset")[1]["id"]

    report = await ex.apply_decisions(company.id, [OptimizerDecision("pause_group", second, None, "слабая")])

    assert report.applied[0].status == "applied" and env.fake.object(second)["status"] == "PAUSED"
    assert env.db.objects.get_by_meta_id(second).status == "PAUSED"
    assert any(r.action == "decision:pause" and r.actor == "optimizer" for r in env.db.audit.list())


async def test_monthly_limit_decrease_is_immediate_and_lowers_meta_cap(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    result = await launched(env, ex, company)

    lowered = await ex.set_monthly_limit(company.id, Money(50000, "USD"))

    assert lowered.status == "done" and env.fake.object(result.campaign_id)["spend_cap"] == 50000
    assert env.db.companies.get(company.id).monthly_limit == Money(50000, "USD")


async def test_monthly_limit_increase_needs_approval_then_raises_cap(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    result = await launched(env, ex, company)

    asked = await ex.set_monthly_limit(company.id, Money(200000, "USD"))
    assert env.db.companies.get(company.id).monthly_limit == Money(100000, "USD")
    approval = ex.create_approval(asked.needs_approval, company_id=company.id)
    raised = await ex.set_monthly_limit(company.id, Money(200000, "USD"), approval.id)

    assert asked.status == "needs_approval" and raised.status == "done"
    assert env.fake.object(result.campaign_id)["spend_cap"] == 200000


async def test_month_rollover_refreshes_spend_cap_to_spent_plus_remaining(env):
    company = env.company(mode="autonomous")
    ex = env.executor()
    result = await launched(env, ex, company)
    await ex.set_monthly_limit(company.id, Money(50000, "USD"))

    outcome = await ex.month_rollover(company.id)

    assert outcome.status == "done" and env.fake.object(result.campaign_id)["spend_cap"] == 50000


class HaltsAfter:
    """Шлюз, после успешного вызова которого поднимается стоп-кран (кнопка нажата, пока шёл запрос)."""

    def __init__(self, inner, env, method):
        self._inner, self._env, self._method = inner, env, method

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name != self._method:
            return attr

        async def call(*args, **kwargs):
            result = await attr(*args, **kwargs)
            self._env.db.settings.set("halted", True)
            return result
        return call


async def test_budget_raise_is_rolled_back_when_halt_rises_during_the_request(env):
    company = env.company(mode="autonomous")
    result = await launched(env, env.executor(), company)
    group = env.live("adset")[0]["id"]
    before = env.fake.object(group)["daily_budget"]
    ex = env.executor(lambda client: HaltsAfter(client, env, "set_daily_budget"))

    report = await ex.apply_decisions(company.id, [OptimizerDecision("scale", group, Money(before + 200, "USD"), "рост")])

    assert result.status == LAUNCHED
    assert report.applied[0].status == "denied" and "Стоп-кран" in report.applied[0].message_ru
    assert env.fake.object(group)["daily_budget"] == before
    assert env.db.objects.get_by_meta_id(group).daily_budget == Money(before, "USD")


async def test_spend_cap_raise_is_rolled_back_when_halt_rises_during_the_request(env):
    company = env.company(mode="autonomous")
    result = await launched(env, env.executor(), company)
    await env.executor().set_monthly_limit(company.id, Money(50000, "USD"))
    capped = env.fake.object(result.campaign_id)["spend_cap"]
    env.db.companies.update(replace(env.db.companies.get(company.id), monthly_limit=Money(100000, "USD")))
    ex = env.executor(lambda client: HaltsAfter(client, env, "set_spend_cap"))

    outcome = await ex.month_rollover(company.id)

    assert outcome.status == "failed" and "Стоп-кран" in outcome.message_ru
    assert env.fake.object(result.campaign_id)["spend_cap"] == capped


async def test_test_level_validates_without_leaving_objects_and_never_activates(env):
    company = env.company()
    plan = env.plan(company)
    env.db.settings.set("level", "test")
    ex = env.executor()

    result = await ex.launch(plan.id, None)

    assert result.status == VALIDATED and env.db.plans.get(plan.id).status == "validated"
    assert env.live("campaign") == [] and env.live("adset") == [] and env.live("ad") == []
    validated = [r for r in env.mutations() if "validate_only" in (r.body.get("execution_options") or [])]
    assert {r.path.split("/")[-1] for r in validated} == {"campaigns", "adsets", "adcreatives", "ads"}
    assert not [r for r in env.mutations() if r.body.get("status") == "ACTIVE"]
    assert not any("idempotent" in k for r in validated for k in r.headers) and env.db.launches.list() == []


async def test_creative_fix_creates_new_ad_in_same_group_and_deletes_rejected(env):
    company = env.company(mode="autonomous")
    asset = env.media_asset(company)
    ex = env.executor()
    await launched(env, ex, company, new_creative=asset.id)
    old = env.live("ad")[0]
    env.fake.disapprove_ad(old["id"])

    outcome = await ex.apply_creative_fix(old["id"], AdVariant(kind="new_creative", primary_text="Новый текст",
                                                              headline="Новый заголовок"))

    ads = env.live("ad")
    new = next(a for a in ads if "исправление" in a["params"]["name"])
    assert outcome.status == "done" and old["id"] not in [a["id"] for a in ads] and len(ads) == 4
    assert new["parent_id"] == old["parent_id"]
    creative = env.fake.object(new["params"]["creative"]["creative_id"])
    assert creative["params"]["object_story_spec"]["photo_data"]["caption"] == "Новый текст"


async def test_creative_fix_in_confirm_mode_waits_for_button(env):
    company = env.company()
    asset = env.media_asset(company)
    ex = env.executor()
    await launched(env, ex, company, new_creative=asset.id)
    old = env.live("ad")[0]["id"]

    outcome = await ex.apply_creative_fix(old, AdVariant(kind="new_creative", primary_text="т", headline="з"))

    assert outcome.status == "needs_approval" and len(env.live("ad")) == 4


async def test_test_level_with_new_creative_sends_no_invented_image_hash(env):
    company = env.company()
    plan = env.plan(company, new_creative=env.media_asset(company).id)
    env.db.settings.set("level", "test")

    result = await env.executor().launch(plan.id, None)

    assert result.status == VALIDATED
    creatives = [r.body for r in env.mutations() if r.path.endswith("/adcreatives")]
    assert creatives and "image_hash" not in str(creatives)
    assert env.live("creative") == []
