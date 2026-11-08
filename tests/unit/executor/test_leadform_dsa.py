from dataclasses import replace

import pytest

from target_ai.executor import PolicyDenied
from target_ai.executor.results import AWAITING_DECISION, LAUNCHED, ROLLED_BACK
from target_ai.fakemeta.errors import FakeError

PRIVACY = "https://coffee.example/privacy"


def reshape(env, plan, **changes):
    data = {**plan.data, **{k: v for k, v in changes.items() if k != "countries"}}
    if "countries" in changes:
        data["groups"] = [{**g, "countries": changes["countries"]} for g in data["groups"]]
    return env.db.plans.update(replace(plan, data=data))


def lead_plan(env, company, **changes):
    plan = env.plan(company)
    return reshape(env, plan, objective="lead_form", **changes)


async def refused(env, ex, plan):
    check_error = None
    with pytest.raises(PolicyDenied) as caught:
        await ex.launch(plan.id, None)
    check_error = caught.value
    assert env.mutations() == [] and env.db.launches.list() == [] and env.db.objects.list() == []
    return check_error


# ------------------------------------------------------------------ лид-форма
async def test_lead_form_launch_without_privacy_url_is_refused_before_creating_anything(env):
    company = env.company(mode="autonomous")
    plan = lead_plan(env, company)

    error = await refused(env, env.executor(), plan)

    assert "политик" in error.reason_ru.lower() and error.code == "privacy_policy_missing"


async def test_lead_form_is_created_with_page_token_before_creatives_and_ctas_point_to_it(env):
    company = env.company(mode="autonomous")
    plan = lead_plan(env, company, privacy_policy_url=PRIVACY)

    result = await env.launch(env.executor(), plan)

    assert result.status == LAUNCHED
    form, = env.fake.objects("leadform")
    sent = env.fake.requests_matching(r"POST \d+/leadgen_forms")[-1]
    page = env.fake.state.data["pages"][company.page_id]
    assert sent.headers["authorization"] == f"Bearer {page['access_token']}"
    assert [q["type"] for q in sent.body["questions"]] == ["FULL_NAME", "PHONE"]
    assert sent.body["privacy_policy"] == {"url": PRIVACY, "link_text": "Политика конфиденциальности"}
    launch = env.db.launches.list()[0]
    assert f"[ta:{env.db.settings.get('install_id')}:L{launch.id}:leadform]" in sent.body["name"]
    assert sent.headers["x-client-idempotent-key"]
    paths = [r.path for r in env.mutations() if r.status == 200]
    assert paths.index(sent.path) < min(i for i, p in enumerate(paths) if p.endswith("/adcreatives"))
    creatives = [r.body["call_to_action"] for r in env.mutations() if r.path.endswith("/adcreatives")]
    assert creatives and all(c == {"type": "SIGN_UP", "value": {"lead_gen_form_id": form["id"]}} for c in creatives)
    assert [o.kind for o in env.db.objects.list(company_id=company.id) if o.meta_id == form["id"]] == ["leadform"]


async def test_lost_form_response_is_adopted_by_tag_without_a_duplicate_form(env):
    company = env.company(mode="autonomous")
    plan = lead_plan(env, company, privacy_policy_url=PRIVACY)
    env.fake.ignore_idempotency_keys = True  # ключ не спасает: дубль предотвращает только сверка по тегу
    env.fake.inject_error(r"POST \d+/leadgen_forms", FakeError(drop_connection=True, after_execute=True))

    result = await env.launch(env.executor(), plan)

    assert result.status == LAUNCHED and len(env.fake.objects("leadform")) == 1


async def test_continue_after_failure_reuses_the_form_and_rollback_removes_it(env):
    company = env.company(mode="autonomous")
    plan = lead_plan(env, company, privacy_policy_url=PRIVACY)
    failed = await env.launch(env.executor(env.flaky(fail_ad_at=2)), plan)
    assert failed.status == AWAITING_DECISION

    resumed = await env.executor().continue_launch(failed.launch_id)
    assert resumed.status == LAUNCHED and len(env.fake.objects("leadform")) == 1

    other = lead_plan(env, env.company(name="Вторая", mode="autonomous"), privacy_policy_url=PRIVACY)
    broken = await env.launch(env.executor(env.flaky(fail_ad_at=1)), other)
    rolled = await env.executor().rollback_launch(broken.launch_id)
    assert rolled.status == ROLLED_BACK and len(env.fake.objects("leadform")) == 1


async def test_other_objectives_never_create_a_form(env):
    company = env.company(mode="autonomous")

    await env.launch(env.executor(), env.plan(company))

    assert env.fake.objects("leadform") == []


# ------------------------------------------------------------------ ЕС (DSA)
async def test_eu_geo_without_dsa_fields_is_refused_before_creating_anything(env):
    company = env.company("EUR", mode="autonomous")
    plan = reshape(env, env.plan(company), countries=["DE"])

    error = await refused(env, env.executor(), plan)

    assert error.code == "dsa_missing" and "ЕС" in error.reason_ru


async def test_eu_geo_with_only_beneficiary_is_refused(env):
    company = env.company("EUR", mode="autonomous")
    plan = reshape(env, env.plan(company), countries=["FR"], dsa_beneficiary="ООО Ромашка")

    await refused(env, env.executor(), plan)


async def test_eu_geo_passes_dsa_fields_to_every_eu_group(env):
    company = env.company("EUR", mode="autonomous")
    plan = reshape(env, env.plan(company), countries=["DE", "US"], dsa_beneficiary="ООО Ромашка", dsa_payor="ИП Иванов")

    result = await env.launch(env.executor(), plan)

    assert result.status == LAUNCHED
    adsets = [r.body for r in env.mutations() if r.path.endswith("/adsets")]
    assert adsets and all(b["dsa_beneficiary"] == "ООО Ромашка" and b["dsa_payor"] == "ИП Иванов" for b in adsets)


async def test_non_eu_geo_sends_no_dsa_fields_even_if_the_plan_has_them(env):
    company = env.company(mode="autonomous")
    plan = reshape(env, env.plan(company), dsa_beneficiary="ООО Ромашка", dsa_payor="ИП Иванов")

    await env.launch(env.executor(), plan)

    adsets = [r.body for r in env.mutations() if r.path.endswith("/adsets")]
    assert adsets and not any("dsa_beneficiary" in b or "dsa_payor" in b for b in adsets)


async def test_check_launch_also_refuses_a_plan_without_required_fields(env):
    company = env.company("EUR", mode="autonomous")
    plan = reshape(env, env.plan(company), countries=["IT"])

    with pytest.raises(PolicyDenied):
        await env.executor().check_launch(plan.id)
