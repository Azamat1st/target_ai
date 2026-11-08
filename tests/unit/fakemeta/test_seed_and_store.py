from target_ai.fakemeta import FakeGraphAPI
from target_ai.meta import MetaClient


async def test_seed_demo_has_three_accounts_with_currencies_and_timezones(client):
    accounts = {a.currency: a for a in await client.list_ad_accounts()}

    assert {c: a.timezone_name for c, a in accounts.items()} == {
        "USD": "America/New_York", "EUR": "Europe/Berlin", "JPY": "Asia/Tokyo"}
    assert all(a.is_active and a.funding_source for a in accounts.values())
    assert accounts["JPY"].min_campaign_group_spend_cap.currency == "JPY"
    assert accounts["USD"].min_campaign_group_spend_cap.amount_minor == 10000


async def test_minimum_budgets_come_in_account_currency_minor_units(client, acct):
    budgets = await client.get_minimum_budgets(acct["JPY"])

    assert budgets["high_freq"].currency == "JPY" and budgets["high_freq"].amount_minor == 750
    assert budgets["imp"] < budgets["high_freq"] < budgets["low_freq"]


async def test_every_account_has_a_foreign_active_campaign(fake, client, acct):
    for account_id in acct.values():
        foreign = [o for o in fake.objects("campaign", account_id) if o["foreign"]]
        assert len(foreign) == 1
        assert fake.state.effective_status(foreign[0]) == "ACTIVE"
        found = await client.find_by_name_tag(account_id, "campaigns", "Чужая")
        assert [row["id"] for row in found] == [foreign[0]["id"]]


async def test_pages_link_to_instagram_business_accounts_with_media(client):
    pages = await client.list_pages()

    assert len(pages) == 3
    assert "access_token" not in pages[0]
    ig_id = await client.get_page_instagram(pages[0]["id"])
    profile = await client.get_ig_profile(ig_id)
    media = await client.get_ig_media(ig_id)
    insights = await client.get_ig_media_insights(media[0]["id"])
    assert profile["followers_count"] > 0 and profile["username"]
    assert len(media) == 6 and {m["media_product_type"] for m in media} == {"FEED", "REELS"}
    assert media[0]["like_count"] > 0 and media[0]["comments_count"] >= 0
    assert set(insights) == {"reach", "saved", "shares", "total_interactions", "views"}


async def test_media_listing_follows_paging_cursors(fake, client):
    ig_id = next(iter(fake.state.data["ig"]))

    media = await client.get_ig_media(ig_id, limit=50)
    fake.clear_requests()
    small_pages = await client._pages(f"{ig_id}/media", {"fields": "id"}, limit=None)  # noqa: SLF001 - проверка курсоров

    assert len(small_pages) == len(media) == 6
    assert len(fake.requests) == 1


async def test_interest_catalog_has_about_sixty_entries_and_search_filters(client):
    yoga = await client.search_interests("йога")
    everything = await client.search_interests("", limit=100)

    assert [i["name"] for i in yoga] == ["Йога"]
    assert yoga[0]["id"] and yoga[0]["path"]
    assert 55 <= len(everything) <= 65


async def test_estimate_audience_narrows_with_filters(client, acct):
    broad = await client.estimate_audience(acct["USD"], {"geo_locations": {"countries": ["US"]}}, "LINK_CLICKS")
    narrow = await client.estimate_audience(
        acct["USD"], {"genders": [1], "age_min": 25, "age_max": 34}, "LINK_CLICKS")

    assert broad is not None and narrow is not None
    assert narrow[1] < broad[1]


async def test_business_discovery_returns_competitor_and_none_for_unknown(fake, client):
    my_ig = next(iter(fake.state.data["ig"]))

    rival = await client.business_discovery(my_ig, "rival_coffee")
    missing = await client.business_discovery(my_ig, "no_such_account")

    assert rival["followers_count"] == 18_400 and rival["media"]["data"]
    assert missing is None


async def test_debug_token_without_app_credentials_reports_unknown_expiry(client):
    info = await client.debug_token()

    assert info.is_valid and not info.expiry_known


async def test_debug_token_with_app_credentials_reads_expiry_and_scopes(fake, make_client):
    info = await make_client(app_id="1", app_secret="s").debug_token()

    assert info.is_valid and info.expires_at is None and "ads_management" in info.scopes


async def test_state_survives_restart_when_stored_in_sqlite(db, clock):
    first = FakeGraphAPI(db.fake_meta, clock)
    first.seed_demo()
    client = MetaClient("t", transport=first.transport, clock=clock)
    account = (await client.list_ad_accounts())[0]
    campaign = await client.create_campaign(account.id, {"name": "[ta:L9:c]", "objective": "OUTCOME_TRAFFIC",
                                                         "special_ad_categories": []})

    second = FakeGraphAPI(db.fake_meta, clock)
    second.seed_demo()  # повторное предзаполнение ничего не дублирует
    again = MetaClient("t", transport=second.transport, clock=clock)

    assert len(await again.list_ad_accounts()) == 3
    assert [r["id"] for r in await again.find_by_name_tag(account.id, "campaigns", "[ta:L9:c]")] == [campaign]
    third_id = await again.create_campaign(account.id, {"name": "n", "objective": "OUTCOME_TRAFFIC",
                                                        "special_ad_categories": []})
    assert int(third_id) > int(campaign)
