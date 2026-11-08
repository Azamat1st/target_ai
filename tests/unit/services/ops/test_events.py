"""Требования брифа по событиям, росту расхода, отчётам и расписанию — через Ops и Scheduler."""

from datetime import UTC, datetime, timedelta

import pytest

from target_ai.scheduler import Scheduler
from target_ai.clock import SimClock
from target_ai.services.ops import OpsContext
from target_ai.services.ops.stats import StatsSync


async def launched(world, **kw):
    company = world.company(**kw)
    await world.launch(company)
    return company


# ---------------------------------------------------------------- R68: по событию на тест
async def test_group_without_impressions_for_six_hours_gives_one_alert_with_figures(world):
    company = await launched(world)
    world.clock.advance(timedelta(hours=7))

    sent = await world.ops.alerts.check_company(company.id)

    assert len(sent) == 4 and all("показов нет" in t and "6+ ч" in t for t in sent)
    assert not any("[ta:" in t for t in world.notifier.texts)
    assert await world.ops.alerts.check_company(company.id) == []


async def test_group_with_fresh_activation_is_not_reported_as_silent(world):
    company = await launched(world)
    world.clock.advance(timedelta(hours=2))
    assert await world.ops.alerts.check_company(company.id) == []


async def test_restricted_account_gives_one_alert_with_status(world):
    company = await launched(world)
    world.fake.state.data["accounts"][company.ad_account_id]["account_status"] = 2

    sent = await world.ops.alerts.check_company(company.id)

    assert len(sent) == 1 and "ограничен" in sent[0] and "статус 2" in sent[0]


async def test_token_expiring_in_five_days_warns_with_day_count(world):
    world.fake.state.data["token"]["expires_at"] = int((world.clock.now() + timedelta(days=5, hours=1)).timestamp())

    sent = await world.ops.alerts.check_token()

    assert len(sent) == 1 and "через 5 дн." in sent[0]
    assert await world.ops.alerts.check_token() == []


async def test_token_far_from_expiry_is_silent(world):
    world.fake.state.data["token"]["expires_at"] = int((world.clock.now() + timedelta(days=30)).timestamp())
    assert await world.ops.alerts.check_token() == []


async def test_invalid_token_gives_one_alert(world):
    world.fake.set_token_valid(False)
    sent = await world.ops.alerts.check_token()
    assert len(sent) == 1 and "Токен" in sent[0]


async def test_error_190_in_a_job_triggers_token_alert_once_instead_of_generic_failure(world):
    company = await launched(world)
    world.fake.set_token_valid(False)  # любой запрос получает 190
    scheduler = Scheduler(world.db, world.clock, {"stats_sync": world.ops.handlers()["stats_sync"]}, world.notifier)

    runs = await scheduler.tick()

    assert all(r.ok for r in runs) and company.id
    token_texts = [t for t in world.notifier.texts if "Токен" in t]
    assert len(token_texts) == 1 and not any("не выполнена" in t for t in world.notifier.texts)


async def test_cpa_above_double_the_weekly_average_gives_one_alert_with_both_prices(world):
    company = await launched(world)
    group = world.adsets(company)[0]
    world.seed(company, group.meta_id, days=7, spend=1000, results=10, back=2)  # цена 1,00 $
    world.seed(company, group.meta_id, days=1, spend=1000, results=4, back=1)    # цена 2,50 $

    sent = await world.ops.alerts.check_company(company.id)

    cpa = [t for t in sent if "цена результата" in t]
    assert len(cpa) == 1 and "2,50 $" in cpa[0] and "1,00 $" in cpa[0] and "2,0×" in cpa[0]


async def test_cpa_below_double_the_average_is_silent(world):
    company = await launched(world)
    group = world.adsets(company)[0]
    world.seed(company, group.meta_id, days=7, spend=1000, results=10, back=2)
    world.seed(company, group.meta_id, days=1, spend=1000, results=8, back=1)
    assert not [t for t in await world.ops.alerts.check_company(company.id) if "цена результата" in t]


# ---------------------------------------------------------------- R35
async def test_on_demand_for_one_company_and_for_all(world):
    first = world.company(name="Кофейня")
    second = world.company(name="Пекарня")

    one = await world.ops.reporting.on_demand(first.id)
    everyone = await world.ops.reporting.on_demand(None)

    assert "Кофейня" in one and "Пекарня" not in one
    assert "Кофейня" in everyone and "Пекарня" in everyone and second.id


# ---------------------------------------------------------------- рост расхода (R30/R51)
async def strong_group(world, **kw):
    company = await launched(world, groups=2, limit_major=3000, **kw)
    world.clock.advance(timedelta(days=4))
    group = world.adsets(company)[0]
    world.seed(company, group.meta_id, days=4, spend=1000, results=10)  # цена 1,00 $ при целевой 2,50 $
    return company, group


async def test_scaling_in_confirm_mode_makes_button_and_leaves_budget_untouched(world):
    company, group = await strong_group(world)

    result = await world.ops.optimization.run_daily(company.id)

    assert result.approval_id and result.buttons[0][0][1] == f"ops:apply:{result.approval_id}"
    assert world.budget(group.meta_id) == 1000
    assert world.db.approvals.get(result.approval_id).status == "pending"
    assert any("Предложение" in line and "1,00 $" in line for line in result.lines) or any(
        "цена результата" in line for line in result.lines)

    applied = await world.ops.optimization.apply_approved(company.id, result.approval_id)

    assert world.budget(group.meta_id) == 1200 and applied.approval_id is None


async def test_scaling_in_autonomous_mode_changes_budget_without_approval(world):
    company, group = await strong_group(world, mode="autonomous")

    result = await world.ops.optimization.run_daily(company.id)

    assert result.approval_id is None and world.budget(group.meta_id) == 1200
    assert world.db.approvals.list(kind="optimizer_scale") == []


async def test_new_scale_offer_cancels_the_previous_unconfirmed_one(world):
    company, _ = await strong_group(world)
    first = await world.ops.optimization.run_daily(company.id)
    world.clock.advance(timedelta(hours=1))

    second = await world.ops.optimization.run_daily(company.id)

    assert first.approval_id != second.approval_id
    assert world.db.approvals.get(first.approval_id).status == "cancelled"
    assert world.db.approvals.get(second.approval_id).status == "pending"


async def test_new_month_raises_spend_cap_within_limit_and_leaves_daily_budgets_to_the_button(world):
    company = await launched(world, groups=2)
    campaign = world.db.objects.list(company_id=company.id, kind="campaign")[0]
    group = world.adsets(company)[0]
    await world.ops.optimization.run_daily(company.id)  # запоминает месяц
    world.seed(company, group.meta_id, days=1, spend=5000, results=3)
    world.clock.advance(timedelta(days=30))

    await world.ops.optimization.run_daily(company.id)

    assert world.fake.object(campaign.meta_id)["spend_cap"] == 105000  # потрачено 50 $ + новый остаток 1000 $
    assert world.budget(group.meta_id) == 1000
    assert any("Новый месяц" in t for t in world.notifier.texts)


# ---------------------------------------------------------------- R31, R29
async def test_fatigue_warning_reaches_the_user_once_a_day(world):
    company = await launched(world, groups=2)
    group = world.adsets(company)[0]
    world.seed(company, group.meta_id, days=1, spend=500, results=1, frequency=3.5)

    await world.ops.optimization.run_daily(company.id)
    await world.ops.optimization.run_daily(company.id)

    warned = [t for t in world.notifier.texts if "частота показов 3,5" in t]
    assert len(warned) == 1 and "[ta:" not in warned[0]


async def test_weak_ad_is_paused_in_meta_while_group_keeps_one_ad(world):
    company = await launched(world, groups=2, limit_major=3000)
    world.clock.advance(timedelta(days=4))
    group = world.adsets(company)[0]
    weak, good = [a for a in world.ads(company) if a.parent_meta_id == group.meta_id]
    world.seed(company, group.meta_id, days=4, spend=1000, results=5)           # цена группы 2,00 $
    world.seed(company, weak.meta_id, days=1, spend=2000, results=1, impressions=2000)  # 20,00 $
    world.seed(company, good.meta_id, days=1, spend=2000, results=19, impressions=2000)

    await world.ops.optimization.run_daily(company.id)

    assert world.fake.object(weak.meta_id)["status"] == "PAUSED"
    assert world.fake.object(good.meta_id)["status"] == "ACTIVE"
    assert world.db.history.list(company_id=company.id, kind="ad_result")


async def test_weak_group_is_paused_in_meta_and_its_outcome_is_recorded(world):
    company = await launched(world, groups=2, limit_major=3000)
    world.clock.advance(timedelta(days=4))
    group = world.adsets(company)[0]
    world.seed(company, group.meta_id, days=4, spend=1000, results=1)  # цена 40,00 $ при целевой 2,50 $

    await world.ops.optimization.run_daily(company.id)

    assert world.fake.object(group.meta_id)["status"] == "PAUSED"
    entry, = world.db.history.list(company_id=company.id, kind="group_result")
    assert entry.outcome == "слабая" and entry.data["days"] == 4


# ---------------------------------------------------------------- R33: расписание
class Fixed:
    def __init__(self, moment):
        self.moment = moment

    def now(self):
        return self.moment


async def test_daily_report_job_fires_at_report_time_in_account_timezone(world):
    company = world.company()
    clock = SimClock(world.db.sim, base=Fixed(datetime(2026, 11, 8, 12, 0, tzinfo=UTC)))  # 07:00 в Нью-Йорке
    world.db.sim.set_offset(timedelta(0))
    world.db.settings.set("report_time", "10:00")
    seen = []

    async def record(cid):
        seen.append(cid)

    scheduler = Scheduler(world.db, clock, {"daily_report": record})
    await scheduler.tick()
    await scheduler.run_until(datetime(2026, 11, 8, 14, 59, tzinfo=UTC))
    assert seen == []

    await scheduler.run_until(datetime(2026, 11, 8, 15, 0, tzinfo=UTC))
    assert seen == [company.id]
    await scheduler.run_until(datetime(2026, 11, 8, 20, 0, tzinfo=UTC))
    assert seen == [company.id]  # раз в сутки


# ---------------------------------------------------------------- статистика: действие по цели и перезапрос
class StubGateway:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    async def get_insights(self, object_id, *, level, since, until, fields=None):
        self.calls.append((level, since, until))
        return [r for r in self.rows if f"{level}_id" in r]


def stat_row(adset_id, day, **extra):
    return {"adset_id": adset_id, "date_start": day, "spend": "1.50", "impressions": "100", "frequency": "1.2",
            "actions": [{"action_type": "link_click", "value": "7"},
                        {"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "3"}],
            **extra}


@pytest.mark.parametrize(("objective", "expected"), [("direct_messages", 3), ("website_traffic", 7)])
async def test_result_comes_from_the_action_of_the_goal_when_row_has_no_results(world, objective, expected):
    company = await launched(world, groups=2)
    plan = world.db.plans.latest(company.id)
    world.db.plans.update(__import__("dataclasses").replace(plan, data={**plan.data, "objective": objective}))
    group = world.adsets(company)[0]
    day = world.ctx.account_today(company).isoformat()
    stub = StubGateway([stat_row(group.meta_id, day)])
    stats = StatsSync(OpsContext(world.db, world.clock, world.executor, lambda _v: stub, world.notifier, None))

    await stats.sync_company(company.id)

    row, = world.db.stats.list(meta_id=group.meta_id)
    assert row.results == expected and row.spend.amount_minor == 150


async def test_second_sync_refetches_only_last_three_days_and_updates_them(world):
    company = await launched(world, groups=2)
    group = world.adsets(company)[0]
    today = world.ctx.account_today(company)
    stub = StubGateway([stat_row(group.meta_id, (today - timedelta(days=1)).isoformat(),
                                 results=[{"values": [{"value": "2"}]}])])
    stats = StatsSync(OpsContext(world.db, world.clock, world.executor, lambda _v: stub, world.notifier, None))

    await stats.sync_company(company.id)
    stub.rows = [stat_row(group.meta_id, (today - timedelta(days=1)).isoformat(), results=[{"values": [{"value": "5"}]}])]
    await stats.sync_company(company.id)

    assert stub.calls[0][1] == today - timedelta(days=31)
    assert stub.calls[2][1] == today - timedelta(days=2)
    row, = world.db.stats.list(meta_id=group.meta_id)
    assert row.results == 5


async def test_halted_system_gets_no_optimization(world):
    company, group = await strong_group(world, mode="autonomous")
    world.db.settings.set("halted", True)

    result = await world.ops.optimization.run_daily(company.id)

    assert result.skipped_reason and world.budget(group.meta_id) == 1000
