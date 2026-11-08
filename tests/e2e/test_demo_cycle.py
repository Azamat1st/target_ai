"""Главный сценарий брифа: компания -> паспорт -> медиаплан -> «Запустить» -> +7 дней -> оптимизатор и отчёт.

Всё через приложение целиком: фейковый Telegram, фейковый Graph API, симулированные часы.
Числа правил (72 ч, 2 целевые цены, 20 %, раз в сутки) — из спецификации §3, а не из кода политики.
"""

from __future__ import annotations

import re
from collections import defaultdict

import pytest
from datetime import timedelta

from target_ai.services.ops.common import strip_tags

from .conftest import OWNER, own_objects, reach_plan
from .llms import ScriptThenOffline, scripted_happy_path

MIN_HOURS_BEFORE_DECISION = 72
MIN_SPEND_TARGET_MULTIPLE = 2
MAX_CHANGE_PCT = 20
MIN_INTERVAL = timedelta(hours=24)
PERFORMANCE_REASON = re.compile(r"[−+]\d+% бюджета")
TARGET_CPA_EUR = 9  # между ценами результата групп в симуляции: часть групп лучше цели, часть хуже


async def run_cycle(e, cpa: int):
    await reach_plan(e, "EUR", cpa)
    await e.tg.press_button(OWNER, "Запустить")
    await e.tg.say(OWNER, "⏩ +7 дней")
    return e.tg.last(OWNER).text


def assert_campaign_live_and_instagram_only(e):
    campaigns = own_objects(e, "campaign")
    adsets = own_objects(e, "adset")
    assert len(campaigns) == 1 and campaigns[0]["status"] == "ACTIVE"
    assert adsets and all(a["status"] in ("ACTIVE", "PAUSED") for a in adsets)
    for adset in adsets:
        targeting = adset["params"]["targeting"]
        assert targeting["publisher_platforms"] == ["instagram"]
        assert set(targeting["instagram_positions"]) <= {"stream", "story", "reels", "explore", "profile_feed"}


def budget_changes(e) -> dict[str, list[tuple]]:
    """Для каждой группы: [(время решения из журнала, было, стало)] по запросам к фейковому Meta и журналу."""
    posts = defaultdict(list)
    for request in e.fake.requests:
        if request.method == "POST" and request.body and "daily_budget" in request.body:
            posts[request.path.rsplit("/", 1)[-1]].append(int(request.body["daily_budget"]))
    times = defaultdict(list)
    for entry in e.db.audit.list():
        if entry.action == "decision:set_budget" and entry.data.get("outcome") == "ok":
            times[entry.data["target"]].append(entry)
    changes = {}
    for adset in own_objects(e, "adset"):
        meta_id = adset["id"]
        previous = int(adset["params"]["daily_budget"])
        rows = []
        for entry, new in zip(times[meta_id], posts[meta_id], strict=True):
            if PERFORMANCE_REASON.search(entry.data["reason"]):  # «потолок на сегодня» — защита лимита, не правило эффективности
                rows.append((entry.created_at, previous, new))
            else:
                assert new < previous, "защита лимита только снижает бюджет"
            previous = new
        changes[meta_id] = rows
    return changes


def assert_changes_follow_rules(e, target_minor: int):
    launched = {o.meta_id: o.created_at for o in e.db.objects.list(kind="adset")}
    changes = budget_changes(e)
    assert any(changes.values()), "за неделю не было ни одного изменения бюджета"
    for meta_id, rows in changes.items():
        last = None
        for when, old, new in rows:
            assert when - launched[meta_id] >= timedelta(hours=MIN_HOURS_BEFORE_DECISION)
            assert abs(new - old) * 100 <= MAX_CHANGE_PCT * old, (meta_id, old, new)
            assert last is None or when - last >= MIN_INTERVAL
            spent = sum(s.spend.amount_minor for s in e.db.stats.list(meta_id=meta_id) if s.date < when.date().isoformat())
            assert spent >= MIN_SPEND_TARGET_MULTIPLE * target_minor, (meta_id, spent)
            last = when


def assert_every_decision_is_explained(e, summary: str):
    decisions = [x for x in e.db.audit.list() if x.action.startswith("decision:") and x.data.get("outcome") == "ok"]
    assert decisions
    for entry in decisions:
        assert strip_tags(entry.data["reason"]) in summary, entry.data["reason"]


async def test_scripted_cycle_redistributes_budget_by_rules_and_explains_every_change(make_e2e):
    e = make_e2e({"llm": scripted_happy_path()})

    summary = await run_cycle(e, TARGET_CPA_EUR)

    assert_campaign_live_and_instagram_only(e)
    assert len(own_objects(e, "adset")) == 3
    assert "⏩ Перемотка на 7" in summary and PERFORMANCE_REASON.search(summary) and "цена результата" in summary
    assert_changes_follow_rules(e, TARGET_CPA_EUR * 100)
    assert_every_decision_is_explained(e, summary)
    asked = {call.schema.__name__ for call in e.app.llm.calls}
    assert {"Passport", "MediaPlanProposal", "AdCopySet", "ComplianceVerdict"} <= asked  # план составила скриптовая модель


async def test_offline_cycle_without_claude_key_runs_end_to_end_and_explains_decisions(make_e2e):
    e = make_e2e()  # OfflineLLM по умолчанию

    summary = await run_cycle(e, 3)

    assert_campaign_live_and_instagram_only(e)
    assert "(демо без Claude)" in e.tg.all_text(OWNER)
    assert "Изменения и события" in summary
    assert_every_decision_is_explained(e, summary)


async def test_cycle_survives_scripted_model_that_runs_dry_midway_by_falling_back_to_offline(make_e2e):
    e = make_e2e({"llm": ScriptThenOffline()})

    summary = await run_cycle(e, 3)

    assert_campaign_live_and_instagram_only(e)
    assert "⏩ Перемотка на 7" in summary


@pytest.mark.xfail(strict=True, reason=(
    "Дефект офлайн-демо (agents/offline.py media_plan + services Planning): без ключа Claude нет найденных интересов, "
    "группа «Интересы» отбрасывается, план остаётся из одной группы (спека: 2–4), оптимизатору нечего перераспределять."))
async def test_offline_demo_plan_has_at_least_two_groups_so_budget_can_be_redistributed(make_e2e):
    e = make_e2e()

    await reach_plan(e, "EUR", 3)
    await e.tg.press_button(OWNER, "Запустить")

    assert len(own_objects(e, "adset")) >= 2


async def test_nothing_is_decided_before_a_group_is_72_hours_old(make_e2e):
    e = make_e2e({"llm": scripted_happy_path()})
    await reach_plan(e, "EUR", TARGET_CPA_EUR)
    await e.tg.press_button(OWNER, "Запустить")

    await e.tg.say(OWNER, "⏩ +2 дня")  # 48 часов: «созреть» группы ещё не могли
    performance = [x for x in e.db.audit.list() if x.action.startswith("decision:")
                   and (PERFORMANCE_REASON.search(x.data["reason"]) or x.action == "decision:pause")]

    assert performance == []
    assert all(a["status"] == "ACTIVE" for a in own_objects(e, "adset"))
