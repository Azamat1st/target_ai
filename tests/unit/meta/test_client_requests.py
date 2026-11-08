import hashlib
import hmac

import pytest

from target_ai.meta import MetaError
from target_ai.money import Money

CAMPAIGN = {"name": "[ta:L1:c] К", "objective": "OUTCOME_TRAFFIC", "special_ad_categories": []}


async def test_sends_bearer_token_user_agent_and_appsecret_proof(fake, make_client, acct):
    client = make_client(app_secret="shh", model_name="claude-x")

    await client.get_ad_account(acct["USD"])

    sent = fake.requests[-1]
    assert sent.headers["authorization"] == "Bearer tok-secret"
    assert sent.headers["user-agent"].startswith("TargetAI/") and "(claude-x)" in sent.headers["user-agent"]
    expected = hmac.new(b"shh", b"tok-secret", hashlib.sha256).hexdigest()
    assert sent.params["appsecret_proof"] == expected


async def test_omits_appsecret_proof_without_secret(fake, client, acct):
    await client.get_ad_account(acct["USD"])

    assert "appsecret_proof" not in fake.requests[-1].params


async def test_create_sends_caller_idempotency_key_and_returns_same_id_on_retry(fake, client, acct):
    first = await client.create_campaign(acct["USD"], CAMPAIGN, idempotency_key="key-1")
    second = await client.create_campaign(acct["USD"], CAMPAIGN, idempotency_key="key-1")

    assert first == second
    assert fake.requests[-1].headers["x-client-idempotent-key"] == "key-1"
    assert len(fake.objects("campaign", acct["USD"])) == 2  # своя + чужая


async def test_validate_only_adds_execution_options_without_key_and_creates_nothing(fake, make_client, acct):
    client = make_client(validate_only=True)

    result = await client.create_campaign(acct["USD"], CAMPAIGN, idempotency_key="key-1")

    sent = fake.requests[-1]
    assert result is None
    assert sent.body["execution_options"] == ["validate_only"]
    assert "x-client-idempotent-key" not in sent.headers
    assert len(fake.objects("campaign", acct["USD"])) == 1


async def test_validate_only_ad_requests_synchronous_review(fake, make_client, acct, build_tree):
    tree = await build_tree(make_client(), acct["USD"])
    validating = make_client(validate_only=True)

    await validating.create_ad(acct["USD"], {"name": "x", "adset_id": tree["adset"], "creative": {"creative_id": tree["creative"]}})

    assert fake.requests[-1].body["execution_options"] == ["validate_only", "synchronous_ad_review"]


async def test_budget_methods_accept_only_money(client, acct):
    with pytest.raises(TypeError):
        await client.set_daily_budget("1", 20.5)  # type: ignore[arg-type]


async def test_budget_currency_is_checked_against_the_objects_own_account(fake, client, acct, build_tree):
    tree = await build_tree(client, acct["USD"])
    before = len(fake.requests_matching("^POST"))

    with pytest.raises(MetaError) as caught:
        await client.set_daily_budget(tree["adset"], Money(2000, "EUR"))
    with pytest.raises(MetaError) as caught_cap:
        await client.set_spend_cap(tree["campaign"], Money(20000, "JPY"))

    assert caught.value.kind == caught_cap.value.kind == "currency_mismatch"
    assert "USD" in caught.value.user_message_ru
    assert len(fake.requests_matching("^POST")) == before


async def test_budget_is_sent_as_minor_units_string(fake, client, acct, build_tree):
    tree = await build_tree(client, acct["JPY"], budget_minor=3000)

    await client.set_daily_budget(tree["adset"], Money(4500, "JPY"))

    assert fake.requests[-1].body == {"daily_budget": "4500"}
    assert fake.object(tree["adset"])["daily_budget"] == 4500


@pytest.mark.parametrize("call", [
    lambda c, t, a: c.create_audience(a, {"name": "x"}),
    lambda c, t, a: c.upload_image(a, b"img"),
    lambda c, t, a: c.upload_video(a, b"vid"),
    lambda c, t, a: c.set_status(t["adset"], "ACTIVE"),
    lambda c, t, a: c.set_daily_budget(t["adset"], Money(3000, "USD")),
    lambda c, t, a: c.set_spend_cap(t["campaign"], Money(30000, "USD")),
    lambda c, t, a: c.delete(t["ad"]),
])
async def test_validate_only_client_refuses_methods_without_a_check_mode(fake, make_client, acct, build_tree, call):
    tree = await build_tree(make_client(), acct["USD"], status="PAUSED")
    before = len(fake.requests)

    with pytest.raises(MetaError) as caught:
        await call(make_client(validate_only=True), tree, acct["USD"])

    assert caught.value.kind == "validate_only_unsupported"
    assert len(fake.requests) == before


async def test_minimum_budgets_in_a_foreign_currency_are_an_error_not_money(fake, client, acct):
    fake.state.data["accounts"][acct["USD"]]["budgets_currency"] = "EUR"

    with pytest.raises(MetaError) as caught:
        await client.get_minimum_budgets(acct["USD"])

    assert caught.value.kind == "currency_mismatch"


async def test_writes_by_object_id_spend_the_accounts_point_budget(client, sleeper, acct, build_tree):
    tree = await build_tree(client, acct["USD"], status="PAUSED")
    sleeper.calls.clear()

    for index in range(25):  # 75 баллов > 60
        await client.set_status(tree["adset"], "ACTIVE" if index % 2 else "PAUSED")

    assert sleeper.calls and max(sleeper.calls) <= 300


async def test_fake_bucket_charges_writes_by_object_id_to_the_account(fake, client, acct, build_tree):
    tree = await build_tree(client, acct["USD"], status="PAUSED")
    fake.enable_rate_limit(points=3, window_seconds=300)

    await client.set_status(tree["adset"], "ACTIVE")
    await client.set_status(tree["adset"], "PAUSED")

    assert any(r.status == 400 and r.path == tree["adset"] for r in fake.requests)


async def test_instagram_data_is_read_only_through_graph_api_endpoints(fake, client):
    ig_id = next(iter(fake.state.data["ig"]))

    await client.get_ig_profile(ig_id)
    await client.get_ig_media(ig_id)
    await client.business_discovery(ig_id, "rival_coffee")

    assert len(fake.requests) == 3
    assert all(r.url.startswith("https://graph.facebook.com/v27.0/") for r in fake.requests)


async def test_targeting_sent_by_client_is_recorded_verbatim(fake, client, acct, build_tree):
    tree = await build_tree(client, acct["USD"])

    sent = next(r for r in fake.requests if r.path.endswith("/adsets") and r.method == "POST")
    assert sent.body["targeting"]["publisher_platforms"] == ["instagram"]
    assert fake.object(tree["adset"])["params"]["targeting"]["instagram_positions"] == ["stream", "story"]
