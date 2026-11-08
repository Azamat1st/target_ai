from decimal import Decimal

import pytest

from target_ai.services.admin import SettingsService


@pytest.fixture
def service(env):
    return SettingsService(env.db, env.clock, env.settings())


async def test_changing_rule_is_saved_and_read_by_executor(env, service):
    reply = await service.update("rule.budget_change_max_pct", "30%")

    assert reply.status == "done"
    assert service.current_rules().budget_change_max_pct == 30
    snapshot_rules = env.executor()._rules()
    assert snapshot_rules.budget_change_max_pct == 30


async def test_decimal_rule_accepts_comma(env, service):
    await service.update("rule.fatigue_frequency", "3,5")

    assert service.current_rules().fatigue_frequency == Decimal("3.5")


@pytest.mark.parametrize("key,text", [
    ("rule.budget_change_max_pct", "120"),
    ("rule.budget_change_max_pct", "0"),
    ("rule.min_hours_before_decision", "721"),
    ("rule.min_hours_before_decision", "двадцать"),
    ("rule.fatigue_frequency", "-1"),
    ("thr.token_days", "1.5"),
    ("report_time", "25:00"),
    ("report_tz", "Mars/Olympus"),
])
async def test_invalid_values_are_rejected_in_russian_and_not_saved(env, service, key, text):
    before = service.current(key)

    reply = await service.update(key, text)

    assert reply.status == "error" and reply.text
    assert service.current(key) == before
    assert env.db.audit.list() == []


async def test_boundary_values_are_accepted(service):
    assert (await service.update("rule.min_hours_before_decision", "720")).status == "done"
    assert (await service.update("rule.budget_change_max_pct", "100")).status == "done"
    assert (await service.update("rule.budget_change_max_pct", "1")).status == "done"


async def test_report_time_timezone_and_threshold_are_stored(env, service):
    await service.update("report_time", "9:30")
    await service.update("report_tz", "Asia/Tashkent")
    await service.update("thr.token_days", "14")

    assert env.db.settings.get("report_time") == "09:30"
    assert env.db.settings.get("report_tz") == "Asia/Tashkent"
    assert env.db.settings.get("alert_thresholds") == {"token_days": 14}


async def test_change_goes_to_journal_with_old_and_new_value(env, service):
    await service.update("rule.budget_change_max_pct", "30")

    entry = env.db.audit.list()[0]
    assert entry.action == "setting_change" and "20 → 30" in entry.data["summary"]


async def test_view_lists_allowed_ids_as_read_only(service):
    view = await service.view()

    assert "5" in view.text and "только в файле .env" in view.text
    assert not any("allowed" in cb for row in view.buttons for _, cb in row)
    assert service.allowed_ids() == (5,)
