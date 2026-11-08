from dataclasses import replace

import pytest

from target_ai.executor import EngagementAudience, ExecutorError
from target_ai.fakemeta.errors import FakeError, audience_terms, permission_denied
from target_ai.meta import MetaError

CREATE = r"POST act_\d+/customaudiences"


def profile_of(env, company):
    return env.db.companies.get(company.id).profile


async def test_creates_engagement_audience_by_the_spec_rule_and_remembers_it(env):
    company = env.company()

    result = await env.executor().ensure_engagement_audience(company.id)

    assert isinstance(result, EngagementAudience) and result.size >= 1000
    sent, = env.fake.requests_matching(CREATE)
    source = sent.body["rule"]["inclusions"]["rules"][0]
    assert sent.body["subtype"] == "ENGAGEMENT" and sent.body["prefill"] == 1
    assert source["event_sources"] == [{"id": company.ig_user_id, "type": "ig_business"}]
    assert source["retention_seconds"] == 365 * 86400
    assert source["filter"]["filters"][0]["value"] == "ig_business_profile_all"
    assert sent.headers["x-client-idempotent-key"]
    assert f"[ta:{env.db.settings.get('install_id')}:E{company.id}:engagement]" in sent.body["name"]
    assert profile_of(env, company)["engagement_audience_id"] == result.audience_id
    registered, = env.db.objects.list(company_id=company.id)
    assert (registered.meta_id, registered.kind, registered.launch_id) == (result.audience_id, "audience", None)


async def test_second_call_and_prefilled_profile_do_not_create_again(env):
    company = env.company()
    ex = env.executor()
    first = await ex.ensure_engagement_audience(company.id)

    second = await ex.ensure_engagement_audience(company.id)

    assert second == first and len(env.fake.requests_matching(CREATE)) == 1


async def test_existing_profile_audience_is_only_read(env):
    company = env.company()
    existing = await env.client.create_audience(company.ad_account_id, {
        "name": "ручная", "subtype": "ENGAGEMENT", "prefill": 1,
        "rule": {"inclusions": {"rules": [{"event_sources": [{"id": company.ig_user_id, "type": "ig_business"}]}]}}})
    env.db.companies.update(replace(company, profile={"engagement_audience_id": existing}))
    env.fake.clear_requests()

    result = await env.executor().ensure_engagement_audience(company.id)

    assert result.audience_id == existing and result.size >= 1000 and env.mutations() == []


async def test_small_instagram_gives_size_under_one_thousand(env):
    company = env.company("EUR")

    result = await env.executor().ensure_engagement_audience(company.id)

    assert result.size < 1000


async def test_test_level_creates_nothing_and_reports_unknown_size(env):
    company = env.company()
    env.db.settings.set("level", "test")

    result = await env.executor().ensure_engagement_audience(company.id)

    assert result.size is None and result.audience_id == "" and result.note
    assert env.mutations() == [] and "engagement_audience_id" not in profile_of(env, company)


async def test_terms_error_is_raised_and_nothing_is_remembered(env):
    company = env.company()
    env.fake.inject_error(CREATE, audience_terms())

    with pytest.raises(MetaError) as caught:
        await env.executor().ensure_engagement_audience(company.id)

    assert caught.value.kind == "audience_terms"
    assert "engagement_audience_id" not in profile_of(env, company) and env.db.objects.list() == []


async def test_lost_response_is_adopted_by_tag_even_if_the_key_does_not_help(env):
    company = env.company()
    env.fake.ignore_idempotency_keys = True
    env.fake.inject_error(CREATE, FakeError(drop_connection=True, after_execute=True))

    result = await env.executor().ensure_engagement_audience(company.id)

    assert len(env.fake.objects("audience")) == 1 and result.audience_id == env.fake.objects("audience")[0]["id"]


async def test_interrupted_attempt_is_found_by_tag_on_the_next_call(env):
    company = env.company()
    ex = env.executor()
    env.fake.inject_error(CREATE, FakeError(drop_connection=True, after_execute=True))
    env.fake.inject_error(r"GET act_\d+/customaudiences", permission_denied())  # сверка по тегу тоже не удалась
    env.fake.ignore_idempotency_keys = True
    with pytest.raises(MetaError):
        await ex.ensure_engagement_audience(company.id)

    result = await ex.ensure_engagement_audience(company.id)

    assert len(env.fake.objects("audience")) == 1 and profile_of(env, company)["engagement_audience_id"] == result.audience_id


async def test_company_without_instagram_is_a_clear_error(env):
    company = env.company()
    env.db.companies.update(replace(company, ig_user_id=None))

    with pytest.raises(ExecutorError):
        await env.executor().ensure_engagement_audience(company.id)
