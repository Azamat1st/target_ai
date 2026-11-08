"""Таск 13, остаток 1: новый медиаплан при работающей кампании считается на остаток месяца целиком."""
from datetime import timedelta

import pytest

from .conftest import ACCOUNTS, CHAT
from .test_planning import campaigns, ready, script


async def _running(env, intake, company):
    await intake.planning.propose(company.id, CHAT)
    assert (await intake.handle_callback(CHAT, env.callback("Запустить"))) is True
    assert len([c for c in campaigns(env) if c["status"] == "ACTIVE"]) == 1


async def test_new_plan_over_running_campaign_is_not_blocked_by_its_budgets(env):
    company, ids = ready(env, limit_major=1000)
    intake = env.intake(script(ids, rounds=2))
    await _running(env, intake, company)
    env.clock.advance(timedelta(days=1))

    await intake.planning.propose(company.id, CHAT)

    plan = env.db.plans.latest(company.id)
    snapshot = await env.executor.build_snapshot(company.id)
    total = sum(g["daily_budget"]["amount_minor"] for g in plan.data["groups"])
    assert plan.data["groups"] and 0 < total <= snapshot.daily_cap.amount_minor, env.last()
    assert "не покрывает минимум Meta" not in env.last()

    assert (await intake.handle_callback(CHAT, env.callback("Запустить"))) is True  # запуск не отклонён потолком

    assert "выше потолка" not in env.all_text()
    active = [c for c in campaigns(env) if c["status"] == "ACTIVE"]
    assert len(active) == 1 and len(campaigns(env)) == 2
    spending = [g for g in env.fake.objects("adset", ACCOUNTS["EUR"])
                if g["status"] == "ACTIVE" and g["campaign_id"] == active[0]["id"]]
    assert sum(g["daily_budget"] for g in spending) <= snapshot.daily_cap.amount_minor


@pytest.mark.parametrize("phrase", ["составь новый медиаплан", "новый план", "сделай новый медиаплан",
                                    "новый медиаплан", "создай новый медиаплан"])
async def test_new_plan_phrases_without_company_name_are_understood(env, phrase):
    company, ids = ready(env)
    intake = env.intake(script(ids))

    handled = await intake.handle_text(CHAT, phrase)

    assert handled and env.db.plans.latest(company.id) is not None
    assert "Для какой компании" not in env.all_text()
