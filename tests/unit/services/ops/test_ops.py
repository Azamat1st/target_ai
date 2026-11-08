from datetime import timedelta
from decimal import Decimal

from target_ai.money import Money
from target_ai.storage import DailyStat


async def test_stats_sync_stores_adset_and_ad_rows_matching_fake_totals(world):
    company = world.company()
    await world.launch(company)
    await world.run_days(2)

    written = await world.ops.stats.sync_company(company.id)

    adsets = world.db.objects.list(company_id=company.id, kind="adset")
    raw = world.fake.state.data["stats"]
    expected_results = sum(day["results"] for a in adsets for day in raw[a.meta_id].values())
    expected_spend = sum(day["spend_minor"] for a in adsets for day in raw[a.meta_id].values())
    rows = [r for r in world.db.stats.list(company_id=company.id) if r.meta_id in {a.meta_id for a in adsets}]
    assert written > len(rows) > 0  # объявления тоже
    assert sum(r.results for r in rows) == expected_results
    assert sum(r.spend.amount_minor for r in rows) == expected_spend


async def test_stats_sync_ignores_foreign_objects(world):
    company = world.company()
    await world.launch(company)
    await world.run_days(1)
    await world.ops.stats.sync_company(company.id)
    foreign = {o["id"] for o in world.fake.objects(account_id=company.ad_account_id) if o.get("foreign")}
    assert not foreign & {r.meta_id for r in world.db.stats.list(company_id=company.id)}


async def test_scheduler_failed_job_does_not_stop_others_and_is_reported(world):
    calls = []

    async def boom(_cid):
        raise RuntimeError("сбой задачи")

    async def fine(cid):
        calls.append(cid)

    from target_ai.scheduler import Scheduler
    company = world.company()
    scheduler = Scheduler(world.db, world.clock, {"stats_sync": boom, "moderation": fine}, world.notifier)

    runs = await scheduler.tick()

    assert any(r.kind == "stats_sync" and not r.ok for r in runs) and calls == [company.id]
    assert any("сбой задачи" in t for t in world.notifier.texts)
    failure = next(t for t in world.notifier.texts if "сбой задачи" in t)
    assert "обновление статистики" in failure and "stats_sync" not in failure


async def test_scheduler_jobs_survive_restart_and_run_only_when_due(world):
    from target_ai.scheduler import Scheduler
    company = world.company()
    seen = []

    async def record(cid):
        seen.append(cid)

    handlers = {"moderation": record}
    await Scheduler(world.db, world.clock, handlers).tick()
    await Scheduler(world.db, world.clock, handlers).tick()  # «перезапуск»: задача уже перенесена
    assert seen == [company.id]

    await Scheduler(world.db, world.clock, handlers).run_until(world.clock.now() + timedelta(minutes=16))
    assert seen == [company.id, company.id]


async def test_alert_for_missing_funding_source_is_sent_once_per_day(world):
    company = world.company()
    world.fake.state.data["accounts"][ACCOUNT_ID]["funding_source"] = None

    first = await world.ops.alerts.check_company(company.id)
    second = await world.ops.alerts.check_company(company.id)

    assert len(first) == 1 and "оплат" in first[0] and second == []
    world.clock.advance(timedelta(days=1))
    assert len(await world.ops.alerts.check_company(company.id)) == 1


async def test_alert_on_spend_over_one_and_half_times_budget(world):
    company = world.company()
    await world.launch(company)
    group = world.db.objects.list(company_id=company.id, kind="adset")[0]
    today = world.clock.now().date().isoformat()
    world.db.stats.upsert(DailyStat(meta_id=group.meta_id, company_id=company.id, date=today,
                                    spend=Money(1600, "USD"), impressions=100, results=3))

    sent = await world.ops.alerts.check_company(company.id)

    assert any("1,5×" in text and "16,00 $" in text for text in sent)


async def test_disapproved_ad_in_confirm_mode_offers_fix_button_then_applies_it(world):
    company = world.company(new_creative=True)
    await world.launch(company)
    ad = world.ads(company)[0]
    world.fake.disapprove_ad(ad.meta_id, "Запрещённый контент")

    assert await world.ops.moderation.check_company(company.id) == 1
    assert await world.ops.moderation.check_company(company.id) == 0  # без повторов
    message = world.notifier.messages[-1]
    assert "Запрещённый контент" in message.text
    label, callback = message.buttons[0][0]
    assert label == "Применить исправление" and callback.startswith("ops:fix:")

    done = await world.ops.moderation.apply_fix(callback.removeprefix("ops:fix:"))

    assert done.status == "done"
    assert world.fake.object(ad.meta_id)["deleted"] is True


async def test_disapproved_ad_in_autonomous_mode_is_replaced_without_button(world):
    company = world.company(mode="autonomous", new_creative=True)
    await world.launch(company)
    ad = world.ads(company)[0]
    world.fake.disapprove_ad(ad.meta_id)

    await world.ops.moderation.check_company(company.id)

    assert world.fake.object(ad.meta_id)["deleted"] is True
    assert world.notifier.messages[-1].buttons == []


async def test_demo_week_transfers_budget_from_weak_to_strong_without_growing_total(world):
    company = world.company(mode="confirm")
    await world.launch(company)
    groups = [g.meta_id for g in world.db.objects.list(company_id=company.id, kind="adset")]
    before = {g: world.fake.object(g)["daily_budget"] for g in groups}

    result = await world.demo.advance(7)

    after = {g: world.fake.object(g)["daily_budget"] for g in groups}
    active = [g for g in groups if world.fake.object(g)["status"] == "ACTIVE"]
    assert len(result.day_lines) == 7 and all("$" in line for line in result.day_lines)
    assert any(after[g] < before[g] for g in groups), (before, after)
    assert any(after[g] > before[g] for g in active), (before, after)
    assert sum(after[g] for g in active) <= sum(before.values())
    moves = [c for c in result.changes if "Оптимизация" in c]
    assert moves and any(ch.isdigit() for ch in moves[0]) and "[ta:" not in result.text
    assert world.notifier.messages[-1].text == result.text  # одно итоговое сообщение


async def test_daily_report_offline_has_figures_and_limit_share(world):
    company = world.company()
    await world.launch(company)
    await world.run_days(2)
    await world.ops.stats.sync_company(company.id)

    text = await world.ops.reporting.daily_text(company.id)

    assert "Расход вчера: " in text and "из 1 000,00 $" in text and "целевой" in text


async def test_weekly_report_writes_group_outcomes_to_history(world):
    company = world.company()
    await world.launch(company)
    await world.run_days(4)
    await world.ops.stats.sync_company(company.id)

    text = await world.ops.reporting.weekly(company.id)

    assert "Недельный разбор" in text
    kinds = {h.kind for h in world.db.history.list(company_id=company.id)}
    assert {"group_result", "ad_result", "weekly_insight"} <= kinds


async def test_new_month_runs_rollover_and_notes_it(world):
    company = world.company()
    await world.launch(company)
    await world.ops.optimization.run_daily(company.id)  # запоминает текущий месяц
    world.clock.advance(timedelta(days=30))

    await world.ops.optimization.run_daily(company.id)

    assert any("Новый месяц" in t for t in world.notifier.texts)


ACCOUNT_ID = "act_100000000000001"
_ = Decimal


async def test_daily_summary_lists_changes_made_by_optimizer_in_last_day(world):
    company = world.company(mode="confirm")
    await world.launch(company)

    result = await world.demo.advance(5)

    moves = [c for c in result.changes if "Оптимизация" in c]
    texts = [await world.ops.reporting.daily_text(company.id)]
    assert moves
    assert "Изменения за сутки: " in texts[0] and "не было" not in texts[0] and "бюджета" in texts[0]


async def test_stop_on_exhausted_month_limit_gives_one_message_not_one_per_group(world):
    company = world.company(mode="confirm", groups=4, limit_major=1000)
    await world.launch(company)
    for adset in world.adsets(company):
        world.seed(company, adset.meta_id, days=1, spend=60_000, results=1, back=1)  # 4 × 600 $ > лимита 1 000 $

    result = await world.ops.optimization.run_daily(company.id)

    exhausted = [line for line in result.lines if "Лимит месяца исчерпан" in line]
    assert len(exhausted) == 1 and "кампании на паузе до" in exhausted[0]
    assert sum("лимит месяца исчерпан" in line.lower() for line in result.lines) == 1


async def test_daily_summary_and_weekly_review_show_changes_from_earlier_days_of_a_rewind(world):
    company = world.company(mode="confirm")
    await world.launch(company)
    result = await world.demo.advance(7)
    assert any("Оптимизация" in c for c in result.changes)
    await world.run_days(2)  # дни без новых решений: изменения остались в окне недели, но не суток

    weekly = await world.ops.reporting.weekly(company.id)

    assert "изменений не было" not in weekly and "бюджета" in weekly
