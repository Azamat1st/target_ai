from target_ai import policy
from target_ai.llm import OfflineLLM
from target_ai.money import Money
from target_ai.services.ops import Ops, OpsContext

from .conftest import CHAT


async def test_questionnaire_to_launch_to_daily_optimization_calls_decide_with_target_price(make_env, monkeypatch):
    env = make_env(level="demo")
    llm = OfflineLLM()
    intake = env.intake(llm)
    await intake.handle_text(CHAT, "добавь кофейню @coffee_x, Берлин, 1500 евро в месяц, нужны сообщения, цена сообщения до 3 евро")
    await env.press(intake, "Демо Германия")
    await env.press(intake, "Studio Yoga Berlin")
    await env.press(intake, "Всё верно")
    await env.press(intake, "Пропустить")
    await env.press(intake, "Составить медиаплан")
    await intake.handle_text(CHAT, "Кофейня GmbH")  # ЕС: получатель и плательщик
    await intake.handle_text(CHAT, "Кофейня GmbH")
    await env.press(intake, "Запустить")

    company = env.db.companies.list()[0]
    assert company.target_cpa == Money(300, "EUR")
    calls = []
    original = policy.decide
    monkeypatch.setattr(policy, "decide", lambda *a, **k: calls.append(k.get("target_cpa")) or original(*a, **k))
    ops = Ops(OpsContext(env.db, env.clock, env.executor, env.factory, env.notifier, llm))

    await ops.optimization.run_daily(company.id)

    assert calls == [Money(300, "EUR")]
