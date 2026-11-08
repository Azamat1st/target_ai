import pytest

from target_ai.fakemeta import errors as fe
from target_ai.fakemeta.errors import FakeError
from target_ai.meta import MetaError

CAMPAIGN = {"name": "[ta:L1:c] К", "objective": "OUTCOME_TRAFFIC", "special_ad_categories": []}

ERROR_TABLE = [
    (fe.token_expired(), "token_expired", 190, 463),
    (fe.token_revoked(), "token_invalid", 190, 467),
    (fe.permission_denied(200), "permission", 200, None),
    (fe.permission_denied(10), "permission", 10, None),
    (fe.permission_denied(294), "permission", 294, None),
    (fe.invalid_parameter("daily_budget"), "invalid_parameter", 100, None),
    (fe.api_version_deprecated(), "api_version", 2635, None),
    (fe.ig_access_denied(), "ig_access", 100, 1815199),
    (fe.budget_too_low(), "budget_too_low", 100, 1885272),
    (fe.spend_cap_too_low(), "spend_cap_too_low", 100, 2446307),
    (fe.ad_disapproved(), "ad_disapproved", 100, 2490427),
    (fe.audience_terms(), "audience_terms", 200, 1870034),
    (FakeError(code=3858152 // 1000, subcode=3858152, message="eu"), "eu_dsa", 3858, 3858152),
    (FakeError(code=1234, subcode=5, message="boom", user_message="Что-то пошло не так"), "unknown", 1234, 5),
]


@pytest.mark.parametrize(("injected", "kind", "code", "subcode"), ERROR_TABLE)
async def test_meta_error_codes_map_to_kind_with_russian_text(fake, client, acct, injected, kind, code, subcode):
    fake.inject_error("GET act_", injected)

    with pytest.raises(MetaError) as caught:
        await client.get_ad_account(acct["USD"])

    error = caught.value
    assert (error.kind, error.code, error.subcode) == (kind, code, subcode)
    assert error.fbtrace_id == "AFakeTrace0001"
    assert any("а" <= ch.lower() <= "я" for ch in error.user_message_ru)


async def test_invalid_parameter_names_blamed_fields(fake, client, acct):
    fake.inject_error("POST", fe.invalid_parameter("daily_budget", "targeting"))

    with pytest.raises(MetaError) as caught:
        await client.create_campaign(acct["USD"], CAMPAIGN)

    assert caught.value.blame_fields == ("daily_budget", "targeting")
    assert "daily_budget" in caught.value.user_message_ru


async def test_unknown_error_message_carries_meta_text_and_codes(fake, client, acct):
    fake.inject_error("GET", FakeError(code=1234, subcode=5, message="boom", user_message="Своё сообщение"))

    with pytest.raises(MetaError) as caught:
        await client.get_ad_account(acct["USD"])

    assert caught.value.user_message_ru == "Meta вернула ошибку: Своё сообщение (код 1234/5)"


@pytest.mark.parametrize("code", [17, 80004, 613])
async def test_rate_limit_waits_the_time_meta_asks_for_then_repeats(fake, client, sleeper, clock, acct, code):
    fake.inject_error("GET", fe.rate_limited(code, minutes=2), times=1)
    started = clock.now()

    account = await client.get_ad_account(acct["USD"])

    assert account.currency == "USD"
    assert sleeper.calls == [120.0]
    assert (clock.now() - started).total_seconds() == 120.0
    assert len(fake.requests_matching("GET act_")) == 2


async def test_rate_limit_without_hint_backs_off_exponentially_and_gives_up_after_five_attempts(fake, client, sleeper, acct):
    no_hint = FakeError(code=4, message="App limit")
    fake.inject_error("GET", no_hint, times=10)

    with pytest.raises(MetaError) as caught:
        await client.get_ad_account(acct["USD"])

    assert caught.value.kind == "rate_limit"
    assert sleeper.calls == [30.0, 60.0, 120.0, 240.0]
    assert len(fake.requests_matching("GET act_")) == 5
    assert caught.value.retry_after == 480.0


async def test_requests_are_not_sent_during_a_rate_limit_block(fake, client, sleeper, acct):
    fake.inject_error("GET", fe.rate_limited(17, minutes=1), times=1)
    await client.get_ad_account(acct["USD"])
    sleeper.calls.clear()

    await client.get_ad_account(acct["EUR"])

    assert sleeper.calls == []  # блокировка уже отсижена; второй запрос идёт сразу


async def test_transient_error_on_read_is_retried(fake, client, sleeper, acct):
    fake.inject_error("GET", fe.transient(), times=2)

    account = await client.get_ad_account(acct["USD"])

    assert account.id == acct["USD"]
    assert sleeper.calls == [2.0, 4.0]


async def test_transient_error_is_raised_after_three_attempts(fake, client, acct):
    fake.inject_error("GET", fe.server_error(), times=5)

    with pytest.raises(MetaError) as caught:
        await client.get_ad_account(acct["USD"])

    assert caught.value.kind == "transient"
    assert len(fake.requests_matching("GET act_")) == 3


async def test_connection_drop_on_read_is_retried(fake, client, acct):
    fake.inject_error("GET", fe.connection_dropped(), times=1)

    account = await client.get_ad_account(acct["USD"])

    assert account.id == acct["USD"]


async def test_connection_drop_before_execution_on_create_is_not_retried_and_outcome_is_unknown(fake, client, acct):
    fake.inject_error("POST", fe.connection_dropped(), times=1)

    with pytest.raises(MetaError) as caught:
        await client.create_campaign(acct["USD"], CAMPAIGN, idempotency_key="k-1")

    assert (caught.value.kind, caught.value.outcome_unknown) == ("network", True)
    assert len(fake.requests_matching("POST act_.*/campaigns")) == 1


async def test_executed_then_dropped_create_leaves_exactly_one_object_and_no_second_post(fake, client, acct):
    fake.inject_error("POST", FakeError(drop_connection=True, after_execute=True), times=1)

    with pytest.raises(MetaError) as caught:
        await client.create_campaign(acct["USD"], CAMPAIGN, idempotency_key="k-1")

    assert caught.value.outcome_unknown is True
    assert len(fake.requests_matching("POST act_.*/campaigns")) == 1
    assert len(fake.objects("campaign", acct["USD"])) == 2  # чужая + одна своя


async def test_server_error_after_ignored_idempotency_key_does_not_duplicate_the_object(fake, client, acct):
    fake.ignore_idempotency_keys = True
    fake.inject_error("POST", FakeError(status=503, code=1, message="late", after_execute=True), times=1)

    with pytest.raises(MetaError) as caught:
        await client.create_campaign(acct["USD"], CAMPAIGN, idempotency_key="k-1")

    assert (caught.value.kind, caught.value.outcome_unknown) == ("transient", True)
    assert len(fake.requests_matching("POST act_.*/campaigns")) == 1
    assert len(fake.objects("campaign", acct["USD"])) == 2


async def test_upload_is_not_retried_after_server_error(fake, client, acct):
    fake.inject_error("POST", fe.server_error(), times=1)

    with pytest.raises(MetaError) as caught:
        await client.upload_image(acct["USD"], b"img")

    assert caught.value.outcome_unknown is True
    assert len(fake.requests_matching("POST act_.*/adimages")) == 1


async def test_create_is_still_retried_after_rate_limit_because_it_was_not_executed(fake, client, sleeper, acct):
    fake.inject_error("POST", fe.rate_limited(17, minutes=1), times=1)

    campaign_id = await client.create_campaign(acct["USD"], CAMPAIGN, idempotency_key="k-1")

    assert fake.object(campaign_id) is not None
    assert sleeper.calls == [60.0]
    assert len(fake.objects("campaign", acct["USD"])) == 2


async def test_read_errors_do_not_claim_unknown_outcome(fake, client, acct):
    fake.inject_error("GET", fe.server_error(), times=5)

    with pytest.raises(MetaError) as caught:
        await client.get_ad_account(acct["USD"])

    assert caught.value.outcome_unknown is False


async def test_account_request_budget_allows_ten_writes_without_pause(client, sleeper, acct):
    for _ in range(10):  # 30 баллов из 60
        await client.create_campaign(acct["USD"], CAMPAIGN)

    assert sleeper.calls == []


async def test_account_request_budget_of_60_points_per_300_seconds_pauses_the_21st_write(client, sleeper, acct):
    for _ in range(21):  # 63 балла > 60 (плюс 1 балл на чтение валюты)
        await client.create_campaign(acct["USD"], CAMPAIGN)

    assert len(sleeper.calls) == 1
    assert 0 < sleeper.calls[0] <= 300


async def test_budgets_of_different_accounts_are_independent(client, sleeper, acct):
    for _ in range(15):
        await client.create_campaign(acct["USD"], CAMPAIGN)
    for _ in range(15):
        await client.create_campaign(acct["EUR"], CAMPAIGN)

    assert sleeper.calls == []


async def test_usage_header_at_ninety_percent_pauses_until_reset(fake, client, sleeper, acct):
    hot = '{"acc_id_util_pct": 95, "reset_time_duration": 45}'
    fake.inject_error("GET", FakeError(status=400, code=100, message="x", headers={"X-Ad-Account-Usage": hot}))
    with pytest.raises(MetaError):
        await client.get_ad_account(acct["USD"])

    await client.get_ad_account(acct["USD"])

    assert sleeper.calls == [45.0]
