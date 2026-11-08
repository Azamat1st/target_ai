from datetime import date

import pytest

from target_ai.fakemeta import Simulator

DAY1, DAY2, DAY3 = date(2026, 11, 8), date(2026, 11, 9), date(2026, 11, 10)


@pytest.fixture
def sim(fake, clock):
    return Simulator(fake, clock)


async def _rows(client, object_id, level, since=DAY1, until=DAY3):
    return await client.get_insights(object_id, level=level, since=since, until=until)


async def test_active_adset_gets_spend_between_085_and_16_of_budget(fake, client, sim, acct, build_tree):
    tree = await build_tree(client, acct["USD"], budget_minor=2000)

    for offset_days in range(30):
        sim.simulate_day(date.fromordinal(DAY1.toordinal() + offset_days))
    rows = await client.get_insights(tree["adset"], level="adset", since=DAY1, until=date(2026, 12, 31))

    spends = [float(r["spend"]) for r in rows]
    assert len(spends) == 30
    assert all(0.85 * 20 <= s <= 1.6 * 20 + 0.01 for s in spends)
    assert sum(spends) / 30 == pytest.approx(20 * 1.05, rel=0.15)


async def test_insights_use_graph_format_with_actions_results_and_cost(fake, client, sim, acct, build_tree):
    tree = await build_tree(client, acct["USD"], goal="CONVERSATIONS")
    sim.simulate_day(DAY1)

    (row,) = await _rows(client, tree["adset"], "adset", until=DAY1)

    assert row["date_start"] == row["date_stop"] == "2026-11-08"
    assert row["adset_id"] == tree["adset"]
    assert {"spend", "impressions", "reach", "frequency", "clicks", "inline_link_clicks", "ctr"} <= set(row)
    types = {a["action_type"] for a in row["actions"]}
    assert "onsite_conversion.messaging_conversation_started_7d" in types
    assert row["results"][0]["indicator"] == "actions:onsite_conversion.messaging_conversation_started_7d"
    result_count = int(row["results"][0]["values"][0]["value"])
    assert result_count > 0 and row["cost_per_result"][0]["values"][0]["value"]
    assert float(row["cost_per_action_type"][0]["value"]) == pytest.approx(float(row["spend"]) / result_count, abs=0.01)


async def test_same_seed_gives_identical_statistics(fake, client, clock, acct, build_tree):
    tree = await build_tree(client, acct["USD"])
    Simulator(fake, clock).simulate_day(DAY1)
    first = await _rows(client, tree["adset"], "adset", until=DAY1)

    fake.state.data["stats"].clear()
    Simulator(fake, clock).simulate_day(DAY1)

    assert await _rows(client, tree["adset"], "adset", until=DAY1) == first


async def test_frequency_grows_over_the_days(fake, client, sim, acct, build_tree):
    tree = await build_tree(client, acct["USD"])
    created = date.fromisoformat(fake.object(tree["adset"])["created_time"][:10])

    for step in range(10):
        sim.simulate_day(date.fromordinal(created.toordinal() + step))
    rows = await client.get_insights(tree["adset"], level="adset", since=created, until=date.fromordinal(created.toordinal() + 9))

    freq = [float(r["frequency"]) for r in rows]
    assert freq[-1] > freq[0] + 0.3


async def test_foreign_and_paused_objects_get_no_statistics(fake, client, sim, acct, build_tree):
    paused = await build_tree(client, acct["USD"], status="PAUSED")
    sim.simulate_day(DAY1)

    assert await client.get_insights(acct["USD"], level="adset", since=DAY1, until=DAY1) == []
    assert fake.state.data["stats"].get(paused["adset"]) is None


async def test_ads_get_rows_and_pending_ads_are_approved_by_a_day_passing(fake, client, sim, acct, build_tree):
    tree = await build_tree(client, acct["USD"])
    assert (await client.get_ad_status(tree["ad"]))["effective_status"] == "PENDING_REVIEW"

    sim.simulate_day(DAY1)

    assert (await client.get_ad_status(tree["ad"]))["effective_status"] == "ACTIVE"
    (ad_row,) = await _rows(client, tree["campaign"], "ad", until=DAY1)
    assert ad_row["ad_id"] == tree["ad"] and ad_row["adset_id"] == tree["adset"]


async def test_jpy_account_statistics_have_no_fraction(fake, client, sim, acct, build_tree):
    tree = await build_tree(client, acct["JPY"], budget_minor=3000)
    sim.simulate_day(DAY1)

    (row,) = await _rows(client, tree["adset"], "adset", until=DAY1)

    assert "." not in row["spend"] and 2550 <= int(row["spend"]) <= 4800


async def test_results_follow_a_hidden_price_per_group(fake, client, sim, acct, build_tree):
    a = await build_tree(client, acct["USD"], budget_minor=5000)
    b = await build_tree(client, acct["USD"], budget_minor=5000)
    for step in range(14):
        sim.simulate_day(date.fromordinal(DAY1.toordinal() + step))

    async def cpa(tree):
        rows = await client.get_insights(tree["adset"], level="adset", since=DAY1, until=date(2026, 12, 31))
        return sum(float(r["spend"]) for r in rows) / sum(int(r["results"][0]["values"][0]["value"]) for r in rows)

    cpa_a, cpa_b = await cpa(a), await cpa(b)
    assert cpa_a != pytest.approx(cpa_b, rel=0.01)  # у групп разная скрытая цена
    assert 0.08 * 50 * 0.7 <= cpa_a <= 0.35 * 50 * 1.4


async def test_campaign_spend_never_exceeds_its_spend_cap_and_stops_with_a_limit_status(fake, client, sim, acct, build_tree):
    tree = await build_tree(client, acct["USD"], budget_minor=5000, spend_cap_minor=10000)
    fake.approve_ad(tree["ad"])

    for step in range(6):
        sim.simulate_day(date.fromordinal(DAY1.toordinal() + step))
    rows = await client.get_insights(tree["adset"], level="adset", since=DAY1, until=date(2026, 12, 31))

    assert 0 < sum(float(r["spend"]) for r in rows) <= 100.0
    assert len(rows) < 6  # после упора в лимит строк больше нет
    assert (await client.get_ad_status(tree["ad"]))["effective_status"] == "CAMPAIGN_PAUSED"
    assert fake.state.effective_status(fake.object(tree["campaign"])) == "WITH_ISSUES"
