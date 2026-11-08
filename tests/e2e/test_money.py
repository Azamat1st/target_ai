"""Денежные сквозные тесты: что бы ни ответила модель и ни случилось в сети, лишних денег не уходит."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal

import pytest

from target_ai.agents.schemas import (
    AdCopySet, AdVariant, GroupProposal, Hypothesis, MediaPlanProposal, Objective, OptimizerOutput,
    OptimizerSuggestion,
)
from target_ai.app import App
from target_ai.fakemeta import errors
from target_ai.money import Money

from .conftest import E2E, OWNER, STRANGER, ADD_TEXT, no_sleep, own_objects, reach_plan
from .llms import ScriptThenOffline, good_plan

MONTHLY_LIMIT_MINOR = 150_000  # 1 500 евро из текста «добавь компанию»
ACTIVE_STATES = ("ACTIVE",)


def harmful_strategist() -> MediaPlanProposal:
    groups = tuple(
        GroupProposal.model_construct(
            name=f"Группа {i}", hypothesis=Hypothesis.BROAD, budget_share=Decimal("1000000000"), interest_keywords=(),
            locations=("DE",), age_min=None, age_max=None, rationale="")
        for i in range(50))
    return MediaPlanProposal.model_construct(
        objective=Objective.DIRECT_MESSAGES, objective_rationale="", groups=groups, notes="")


def harmful_optimizer(times: int = 40) -> list[OptimizerOutput]:
    suggestion = OptimizerSuggestion.model_construct(action="increase_budget", target="все группы", reason="+500% всем")
    return [OptimizerOutput.model_construct(explanations=(), suggestions=(suggestion,) * 3)] * times


def fake_discount_copy() -> AdCopySet:
    return AdCopySet(variants=(
        AdVariant(kind="new_creative", primary_text="Скидка 70% только сегодня, доставка бесплатно", headline="Скидка 70%", cta="INSTAGRAM_MESSAGE"),
        AdVariant(kind="new_creative", primary_text="Только сегодня скидка 70% на всё", headline="Скидка", cta="INSTAGRAM_MESSAGE")))


def month_spend(e: E2E) -> dict[str, int]:
    """Фактический расход компании по календарным месяцам (из статистики, снятой с фейкового Meta)."""
    totals: dict[str, int] = defaultdict(int)
    groups = {a["id"] for a in own_objects(e, "adset")}  # объявления входят в расход группы: считаем только группы
    for stat in e.db.stats.list(company_id=1):
        if stat.meta_id not in groups:
            continue
        totals[stat.date[:7]] += stat.spend.amount_minor
    return totals


def posted(e: E2E) -> str:
    """Всё, что приложение отправило в Meta (изменяющие запросы), одним текстом."""
    return json.dumps([(r.path, r.params, r.body) for r in e.fake.requests if r.method != "GET"], ensure_ascii=False)


AD_TEXT_KEYS = {"message", "name", "description", "body", "title", "link_description", "caption"}
DISCOUNT = re.compile(r"\b70\s*%|скидк", re.IGNORECASE)


def ad_texts(e: E2E) -> list[str]:
    """Тексты объявлений, ушедшие в Meta: поля креативов и объявлений из POST adcreatives / ads."""
    found: list[str] = []

    def walk(node, key=""):
        if isinstance(node, str) and node[:1] in "{[":
            try:
                node = json.loads(node)
            except ValueError:
                pass
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key in AD_TEXT_KEYS:
            found.append(node)

    for request in e.fake.requests_matching(r"POST .*/(adcreatives|ads)$"):
        walk(request.body)
        walk(request.params)
    return found


async def test_harmful_models_cannot_push_spend_over_the_monthly_limit_in_31_days(make_e2e):
    llm = ScriptThenOffline({MediaPlanProposal: [harmful_strategist()] * 3, OptimizerOutput: harmful_optimizer()})
    e = make_e2e({"llm": llm})
    await reach_plan(e, "EUR", 9)
    e.db.companies.update(replace(e.db.companies.list()[0], mode="autonomous"))  # без кнопок: худший случай
    await e.tg.press_button(OWNER, "Запустить")

    for _ in range(31):
        await e.tg.say(OWNER, "⏩ +1 день")
        assert all(spent <= MONTHLY_LIMIT_MINOR for spent in month_spend(e).values()), month_spend(e)
        active = sum(int(a["daily_budget"]) for a in own_objects(e, "adset") if a["status"] in ACTIVE_STATES)
        assert active <= MONTHLY_LIMIT_MINOR

    assert len(own_objects(e, "adset")) <= 4  # 50 групп от модели не дошли до Meta
    assert HarmfulCalls.seen(llm) >= {"MediaPlanProposal", "OptimizerOutput"}


class HarmfulCalls:
    @staticmethod
    def seen(llm) -> set[str]:
        return {call.schema.__name__ for call in llm.calls}


async def test_invented_discount_never_reaches_meta(make_e2e):
    e = make_e2e({"llm": ScriptThenOffline({MediaPlanProposal: [good_plan()], AdCopySet: [fake_discount_copy()] * 2})})

    await reach_plan(e, "EUR", 3)
    plan_text = e.tg.last(OWNER).text
    await e.tg.press_button(OWNER, "Запустить")

    texts = ad_texts(e)
    assert texts, "тексты объявлений в запросах к Meta не найдены — тест ничего не проверяет"
    assert not [x for x in texts if DISCOUNT.search(x)]
    assert "в фактах компании его нет" in plan_text  # пользователю объяснили, почему объявление убрано
    assert len(own_objects(e, "campaign")) == 1


@pytest.mark.parametrize(("currency", "daily_minor", "cap_minor"), [
    ("USD", 6521, 150_000),   # 1 500 $ / 23 дня (8–30 ноября) = 65,21 $
    ("EUR", 6521, 150_000),
    ("JPY", 6818, 150_000),   # иена без дробной части: 150 000 ¥ / 22 дня (по Токио уже 9 ноября) = 6 818 ¥
])
async def test_amounts_sent_to_meta_are_in_the_currencys_minor_units(make_e2e, currency, daily_minor, cap_minor):
    e = make_e2e({"llm": ScriptThenOffline({MediaPlanProposal: [good_plan()]})})

    await reach_plan(e, currency, 3)
    await e.tg.press_button(OWNER, "Запустить")

    adsets, campaign = own_objects(e, "adset"), own_objects(e, "campaign")[0]
    assert sum(int(a["params"]["daily_budget"]) for a in adsets) == daily_minor
    assert int(campaign["params"]["spend_cap"]) == cap_minor


async def test_double_press_and_repeated_update_make_one_campaign(make_e2e):
    e = make_e2e()
    await reach_plan(e, "EUR", 3)
    data = e.tg.last(OWNER).callbacks[0]

    await e.tg.press(OWNER, data, update_id=9001)
    await e.tg.press(OWNER, data)  # второе нажатие новым апдейтом
    await e.tg.press(OWNER, data, update_id=9001)  # Telegram доставил тот же апдейт повторно

    assert len(own_objects(e, "campaign")) == 1
    assert len(e.db.launches.list()) == 1
    assert len([r for r in e.fake.requests_matching(r"POST .*/campaigns") if "[ta:" in str(r.body)]) == 1


async def test_crash_mid_launch_then_restart_asks_continue_or_rollback_and_spends_nothing(make_e2e, tmp_path):
    e = make_e2e()
    await reach_plan(e, "EUR", 3)
    e.fake.inject_error(r"POST .*/ads$", errors.server_error(), times=50)
    await e.tg.press_button(OWNER, "Запустить")
    launch = e.db.launches.list()[0]
    e.db.launches.update(replace(launch, status="in_progress"))  # процесс убит, не успев записать исход
    e.db.close()

    again = E2E.__new__(E2E)  # новый процесс: те же настройки и та же база, новый Telegram
    again.settings = e.settings
    from support.fake_telegram import FakeTelegram
    again.tg = FakeTelegram()
    again.app = App.create(e.settings, overrides={
        "bot_session": again.tg.session, "meta_sleeper": no_sleep, "tick_seconds": 3600})
    again.tg.bind(again.app)
    await again.app.startup()
    await again.app._ticker.stop()

    text = again.tg.all_text(OWNER)
    assert "Довести" in " ".join(l for m in again.tg.to(OWNER) for l in m.labels)
    assert "Откатить" in " ".join(l for m in again.tg.to(OWNER) for l in m.labels)
    assert not [o for o in own_objects(again, "campaign") if o["status"] == "ACTIVE"]
    await again.tg.say(OWNER, "⏩ +1 день")
    assert sum(s.spend.amount_minor for s in again.db.stats.list()) == 0, text
    await again.tg.press_button(OWNER, "Откатить")
    assert not [o for o in own_objects(again, "campaign") if o["status"] == "ACTIVE"]
    again.db.close()


async def test_stranger_is_refused_on_message_and_on_someone_elses_confirm_button(make_e2e):
    e = make_e2e()
    await reach_plan(e, "EUR", 3)
    launch_button = e.tg.last(OWNER).callbacks[0]

    await e.tg.say(STRANGER, "запусти всё")
    await e.tg.press(STRANGER, launch_button)

    assert e.tg.texts(STRANGER) == ["Доступ закрыт."]
    assert [(a.text, a.show_alert) for a in e.tg.answers][-1] == ("Доступ закрыт.", True)
    assert not own_objects(e, "campaign") and not e.db.launches.list()


async def test_foreign_campaigns_are_never_written_to_across_a_week_with_stop_and_resume(make_e2e):
    e = make_e2e()
    await reach_plan(e, "EUR", 3)
    await e.tg.press_button(OWNER, "Запустить")
    foreign = {o["id"]: (o["status"], o.get("daily_budget")) for o in e.fake.objects() if o["foreign"]}
    assert foreign

    await e.tg.say(OWNER, "⏩ +7 дней")
    await e.tg.say(OWNER, "🛑 Стоп-кран")

    writes = [r for r in e.fake.requests if r.method != "GET" and any(i in r.path for i in foreign)]
    assert writes == []
    assert {o["id"]: (o["status"], o.get("daily_budget")) for o in e.fake.objects() if o["foreign"]} == foreign


async def test_stop_pauses_everything_and_nothing_turns_on_by_itself_afterwards(make_e2e):
    e = make_e2e()
    await reach_plan(e, "EUR", 3)
    await e.tg.press_button(OWNER, "Запустить")
    await e.tg.say(OWNER, "🛑 Стоп-кран")
    before = len(e.fake.requests)
    assert all(o["status"] == "PAUSED" for o in own_objects(e, "campaign"))  # кампания на паузе — группы и объявления не показываются

    await e.tg.say(OWNER, "⏩ +3 дня")
    company_id = e.db.companies.list()[0].id
    await e.tg.press(OWNER, f"cmp:resume:{company_id}")

    after = e.fake.requests[before:]
    enabling = [r for r in after if r.method == "POST" and r.body and r.body.get("status") == "ACTIVE"]
    assert enabling == []
    assert all(o["status"] == "PAUSED" for o in own_objects(e, "campaign"))
    assert sum(s.spend.amount_minor for s in e.db.stats.list() if s.date > "2026-11-08") == 0
