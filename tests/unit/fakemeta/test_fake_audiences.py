import pytest

from target_ai.fakemeta import errors as fe
from target_ai.meta import MetaError

RETARGETING_RULE = {"inclusions": {"operator": "or", "rules": [{
    "event_sources": [{"id": "IG", "type": "ig_business"}], "retention_seconds": 365 * 86400,
    "filter": {"operator": "and", "filters": [{"field": "event", "operator": "eq", "value": "ig_business_profile_all"}]},
}]}}


def _params(ig_id):
    rule = {"inclusions": {"operator": "or", "rules": [{**RETARGETING_RULE["inclusions"]["rules"][0],
                                                       "event_sources": [{"id": ig_id, "type": "ig_business"}]}]}}
    return {"name": "Вовлечённые", "subtype": "ENGAGEMENT", "prefill": 1, "rule": rule}


def _ig_of(fake, account_id):
    page = next(p for p in fake.state.data["pages"].values() if p["account_id"] == account_id)
    return page["instagram_business_account"]["id"]


async def test_demo_engagement_audience_is_big_for_one_instagram_and_small_for_another(fake, client, acct):
    sizes = {}
    for currency in ("USD", "EUR"):
        ig = _ig_of(fake, acct[currency])
        audience = await client.create_audience(acct[currency], _params(ig))
        sizes[currency] = await client.get_engagement_audience_size(audience)

    assert sizes["USD"] >= 1000 and sizes["EUR"] < 1000


async def test_engagement_size_is_deterministic_for_the_same_instagram(fake, client, acct):
    ig = _ig_of(fake, acct["USD"])

    first = await client.get_engagement_audience_size(await client.create_audience(acct["USD"], _params(ig)))
    second = await client.get_engagement_audience_size(await client.create_audience(acct["USD"], _params(ig)))

    assert first == second


async def test_engagement_audience_without_event_sources_is_rejected(client, acct):
    with pytest.raises(MetaError) as caught:
        await client.create_audience(acct["USD"], {"name": "x", "subtype": "ENGAGEMENT", "prefill": 1})

    assert caught.value.code == 100 and "rule" in caught.value.blame_fields


async def test_create_audience_with_same_idempotency_key_returns_same_id(fake, client, acct):
    ig = _ig_of(fake, acct["USD"])

    first = await client.create_audience(acct["USD"], _params(ig), idempotency_key="k-1")
    second = await client.create_audience(acct["USD"], _params(ig), idempotency_key="k-1")

    assert first == second and len(fake.objects("audience", acct["USD"])) == 1


async def test_audience_terms_error_surfaces_as_audience_terms_kind(fake, client, acct):
    fake.inject_error(r"POST act_\d+/customaudiences", fe.audience_terms())

    with pytest.raises(MetaError) as caught:
        await client.create_audience(acct["USD"], _params(_ig_of(fake, acct["USD"])))

    assert caught.value.kind == "audience_terms"


async def test_audiences_can_be_found_by_name_tag(fake, client, acct):
    ig = _ig_of(fake, acct["USD"])
    created = await client.create_audience(acct["USD"], {**_params(ig), "name": "Вовлечённые [ta:abcd1234:E1:engagement]"})
    await client.create_audience(acct["USD"], {**_params(ig), "name": "Чужая"})

    found = await client.find_by_name_tag(acct["USD"], "customaudiences", "[ta:abcd1234:E1:engagement]")

    assert [row["id"] for row in found] == [created]
