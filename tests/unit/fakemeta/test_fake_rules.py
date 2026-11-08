import pytest

from target_ai.fakemeta import errors as fe
from target_ai.meta import MetaError
from target_ai.money import Money


def _targeting(**extra):
    base = {"publisher_platforms": ["instagram"], "geo_locations": {"countries": ["US"]}}
    return {**base, **extra}


async def _campaign(client, account):
    return await client.create_campaign(account, {"name": "k", "objective": "OUTCOME_TRAFFIC", "special_ad_categories": []})


def _adset(campaign, targeting, budget=Money(2000, "USD")):
    return {"name": "g", "campaign_id": campaign, "daily_budget": budget, "optimization_goal": "LINK_CLICKS",
            "billing_event": "IMPRESSIONS", "targeting": targeting}


async def test_narrow_targeting_without_advantage_audience_is_rejected(client, acct):
    campaign = await _campaign(client, acct["USD"])

    with pytest.raises(MetaError) as caught:
        await client.create_adset(acct["USD"], _adset(campaign, _targeting(genders=[1], age_max=40)))

    assert caught.value.kind == "invalid_parameter"
    assert caught.value.blame_fields == ("targeting.targeting_automation.advantage_audience",)


async def test_narrow_targeting_with_explicit_advantage_audience_is_accepted(client, acct):
    campaign = await _campaign(client, acct["USD"])
    targeting = _targeting(genders=[1], age_max=40, targeting_automation={"advantage_audience": 0})

    assert await client.create_adset(acct["USD"], _adset(campaign, targeting))


async def test_fake_remembers_non_instagram_platforms_so_tests_can_see_the_real_request(fake, client, acct):
    campaign = await _campaign(client, acct["USD"])
    targeting = _targeting(publisher_platforms=["facebook", "instagram"])

    adset_id = await client.create_adset(acct["USD"], _adset(campaign, targeting))

    assert fake.object(adset_id)["params"]["targeting"]["publisher_platforms"] == ["facebook", "instagram"]


async def test_spend_cap_below_account_minimum_gives_2446307(client, acct):
    with pytest.raises(MetaError) as caught:
        await client.create_campaign(acct["USD"], {"name": "k", "objective": "OUTCOME_TRAFFIC", "special_ad_categories": [],
                                                   "spend_cap": Money(9999, "USD")})

    assert (caught.value.kind, caught.value.subcode) == ("spend_cap_too_low", 2446307)


async def test_spend_cap_at_account_minimum_is_accepted(client, acct):
    campaign = await client.create_campaign(acct["USD"], {"name": "k", "objective": "OUTCOME_TRAFFIC",
                                                          "special_ad_categories": [], "spend_cap": Money(10000, "USD")})

    await client.set_spend_cap(campaign, Money(20000, "USD"))
    with pytest.raises(MetaError) as caught:
        await client.set_spend_cap(campaign, Money(500, "USD"))

    assert caught.value.subcode == 2446307


async def test_daily_budget_below_minimum_gives_budget_too_low(client, acct):
    campaign = await _campaign(client, acct["USD"])

    with pytest.raises(MetaError) as caught:
        await client.create_adset(acct["USD"], _adset(campaign, _targeting(), Money(100, "USD")))

    assert caught.value.kind == "budget_too_low"


async def test_disapproved_ad_cannot_be_enabled_and_exposes_review_feedback(fake, client, acct, build_tree):
    tree = await build_tree(client, acct["USD"], status="PAUSED")
    fake.disapprove_ad(tree["ad"], "Запрещённое утверждение")

    with pytest.raises(MetaError) as caught:
        await client.set_status(tree["ad"], "ACTIVE")
    status = await client.get_ad_status(tree["ad"])

    assert (caught.value.kind, caught.value.subcode) == ("ad_disapproved", 2490427)
    assert status["effective_status"] == "DISAPPROVED"
    assert "Запрещённое утверждение" in status["ad_review_feedback"]["global"]
    assert status["issues_info"][0]["error_summary"]


async def test_new_active_ad_waits_for_review_then_becomes_active(fake, client, acct, build_tree):
    tree = await build_tree(client, acct["USD"])
    assert (await client.get_ad_status(tree["ad"]))["effective_status"] == "PENDING_REVIEW"

    fake.approve_ad(tree["ad"])

    assert (await client.get_ad_status(tree["ad"]))["effective_status"] == "ACTIVE"


async def test_paused_parent_is_reflected_in_effective_status(fake, client, acct, build_tree):
    tree = await build_tree(client, acct["USD"])
    fake.approve_ad(tree["ad"])

    await client.set_status(tree["adset"], "PAUSED")

    assert (await client.get_ad_status(tree["ad"]))["effective_status"] == "ADSET_PAUSED"


async def test_same_idempotency_key_with_different_parameters_is_an_error(client, acct):
    await client.create_campaign(acct["USD"], {"name": "a", "objective": "OUTCOME_TRAFFIC", "special_ad_categories": []},
                                 idempotency_key="same")

    with pytest.raises(MetaError) as caught:
        await client.create_campaign(acct["USD"], {"name": "b", "objective": "OUTCOME_TRAFFIC", "special_ad_categories": []},
                                     idempotency_key="same")

    assert caught.value.subcode == 1349016


async def test_delete_hides_object_from_lists_and_blocks_reads_by_tag(fake, client, acct, build_tree):
    tree = await build_tree(client, acct["USD"], status="PAUSED")

    await client.delete(tree["ad"])

    assert await client.find_by_name_tag(tree["adset"], "ads", "[ta:L1:a]") == []
    assert (await client.get_ad_status(tree["ad"]))["effective_status"] == "DELETED"


async def test_find_by_name_tag_returns_only_tagged_children(client, acct, build_tree):
    tree = await build_tree(client, acct["USD"], status="PAUSED")

    found = await client.find_by_name_tag(acct["USD"], "campaigns", "[ta:L1:c]")

    assert [row["id"] for row in found] == [tree["campaign"]]


async def test_lookalike_with_country_is_rejected_and_without_is_accepted(client, acct):
    origin = await client.create_audience(acct["USD"], {"name": "base", "subtype": "CUSTOM"})

    with pytest.raises(MetaError) as caught:
        await client.create_audience(acct["USD"], {"name": "ll", "subtype": "LOOKALIKE", "origin_audience_id": origin,
                                                   "lookalike_spec": {"type": "similarity", "country": "US"}})
    created = await client.create_audience(acct["USD"], {"name": "ll", "subtype": "LOOKALIKE", "origin_audience_id": origin,
                                                         "lookalike_spec": {"type": "similarity"}})

    assert caught.value.blame_fields == ("lookalike_spec.country",)
    assert await client.get_engagement_audience_size(created) > 0


async def test_inject_error_applies_only_for_given_number_of_times(fake, client, acct):
    fake.inject_error("GET act_", fe.permission_denied(), times=1)

    with pytest.raises(MetaError):
        await client.get_ad_account(acct["USD"])

    assert (await client.get_ad_account(acct["USD"])).id == acct["USD"]


async def test_expired_token_state_fails_every_request(fake, client):
    fake.set_token_valid(False)

    with pytest.raises(MetaError) as caught:
        await client.list_ad_accounts()

    assert caught.value.kind == "token_expired"


async def test_fake_rate_limit_bucket_answers_with_limit_error_that_client_waits_out(fake, client, sleeper, acct):
    fake.enable_rate_limit(points=5, window_seconds=300)

    for _ in range(8):
        await client.get_ad_account(acct["USD"])

    assert any(wait >= 60 for wait in sleeper.calls)
    assert any(r.status == 400 for r in fake.requests)


async def _eu_adset(client, account, countries, **extra):
    campaign = await _campaign(client, account)
    targeting = _targeting(geo_locations={"countries": countries})
    return await client.create_adset(account, {**_adset(campaign, targeting), **extra})


async def test_adset_with_eu_country_without_dsa_fields_gives_3858152(client, acct):
    with pytest.raises(MetaError) as caught:
        await _eu_adset(client, acct["USD"], ["US", "DE"])

    assert caught.value.kind == "eu_dsa" and caught.value.subcode == 3858152


async def test_adset_with_only_one_dsa_field_is_still_rejected(client, acct):
    with pytest.raises(MetaError) as caught:
        await _eu_adset(client, acct["USD"], ["FR"], dsa_beneficiary="ООО Ромашка")

    assert caught.value.subcode == 3858152


async def test_adset_with_eu_country_and_both_dsa_fields_is_accepted(fake, client, acct):
    adset = await _eu_adset(client, acct["USD"], ["DE"], dsa_beneficiary="ООО Ромашка", dsa_payor="ООО Ромашка")

    assert fake.object(adset)["params"]["dsa_payor"] == "ООО Ромашка"


@pytest.mark.parametrize("countries", [["US"], ["GB", "CH", "NO"]])
async def test_adset_outside_the_eu_needs_no_dsa_fields(client, acct, countries):
    assert await _eu_adset(client, acct["USD"], countries)


def _photo_creative(image_hash, page):
    return {"name": "k", "object_story_spec": {"page_id": page["id"], "photo_data": {"image_hash": image_hash}}}


async def test_creative_with_unknown_image_hash_is_rejected_with_blame_field(fake, client, acct):
    page = next(iter(fake.state.data["pages"].values()))

    with pytest.raises(MetaError) as caught:
        await client.create_creative(acct["USD"], _photo_creative("0" * 32, page))

    assert caught.value.code == 100 and "object_story_spec.photo_data.image_hash" in caught.value.blame_fields


async def test_creative_with_an_uploaded_image_hash_is_accepted(fake, client, acct):
    page = next(iter(fake.state.data["pages"].values()))
    image_hash = await client.upload_image(acct["USD"], b"jpeg")

    assert await client.create_creative(acct["USD"], _photo_creative(image_hash, page))


async def test_creative_from_unknown_instagram_media_is_rejected(fake, client, acct):
    page = next(iter(fake.state.data["pages"].values()))
    params = {"name": "k", "object_id": page["id"], "source_instagram_media_id": "999"}

    with pytest.raises(MetaError) as caught:
        await client.create_creative(acct["USD"], params)

    assert "source_instagram_media_id" in caught.value.blame_fields
