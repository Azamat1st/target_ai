"""R24: правка плана словами без ключа Claude (OfflineLLM) — простые правки применяются, остальное честно отклоняется."""

from decimal import Decimal

from .test_planning import CHAT, ready

from target_ai.llm import OfflineLLM


async def edited_plan(env, request: str | None):
    company, _ = ready(env, profile={"demo": True})
    intake = env.intake(OfflineLLM())
    await intake.planning.propose(company.id, CHAT)
    if request:
        await env.press(intake, "Изменить")
        await intake.handle_text(CHAT, request)
    return env.db.plans.latest(company.id)


def shares(plan) -> list[Decimal]:
    groups = plan.data["groups"]
    total = sum(Decimal(str(g["daily_budget"]["amount_minor"])) for g in groups)
    return [Decimal(str(g["daily_budget"]["amount_minor"])) / total for g in groups]


async def test_more_budget_on_interests_changes_shares_and_says_so(env):
    base = await edited_plan(env, None)
    plan = await edited_plan(env, "больше на интересы")

    assert plan.version == 2 and shares(plan) != shares(base)
    assert "Увеличил долю группы «Интересы»" in env.last()


async def test_unknown_edit_is_declined_honestly_instead_of_silently_repeating(env):
    await edited_plan(env, "сделай текст веселее")

    assert "Без ключа Claude понимаю только простые правки" in env.last()


async def test_removing_a_group_that_is_not_in_the_plan_explains_why_plan_is_unchanged(env):
    await edited_plan(env, "убери ретаргетинг")

    assert "Ретаргетинг" in env.last() and "план не изменился" in env.last()


async def test_cannot_drop_below_two_groups(env):
    await edited_plan(env, "убери интересы")

    assert "минимум 2" in env.last()


async def test_live_company_without_claude_key_is_told_to_add_key_not_shown_internal_code(env):
    company, _ = ready(env)
    intake = env.intake(OfflineLLM())
    await intake.planning.propose(company.id, CHAT)

    assert "ANTHROPIC_API_KEY" in env.last() and "needs_review" not in env.last()


async def test_edits_accumulate_and_a_declined_edit_rolls_nothing_back(env):
    company, _ = ready(env, profile={"demo": True})
    intake = env.intake(OfflineLLM())
    await intake.planning.propose(company.id, CHAT)
    base = shares(env.db.plans.latest(company.id))
    for request in ("больше на интересы", "сделай текст веселее"):
        await env.press(intake, "Изменить")
        await intake.handle_text(CHAT, request)
        if request.startswith("больше"):
            edited = shares(env.db.plans.latest(company.id))

    plan = env.db.plans.latest(company.id)
    assert plan.version == 3 and edited != base and shares(plan) == edited
    assert "Без ключа Claude понимаю только простые правки" in env.last()


async def test_second_edit_builds_on_the_first_one(env):
    company, _ = ready(env, profile={"demo": True})
    intake = env.intake(OfflineLLM())
    await intake.planning.propose(company.id, CHAT)
    await env.press(intake, "Изменить")
    await intake.handle_text(CHAT, "больше на интересы")
    after_one = shares(env.db.plans.latest(company.id))
    await env.press(intake, "Изменить")
    await intake.handle_text(CHAT, "больше на интересы")

    # вторая правка сдвинула долю ещё дальше от исходной (а не повторила первую): доля интересов выросла
    # без накопления вторая версия повторила бы первую (доли 0,3/0,7)
    assert shares(env.db.plans.latest(company.id)) != after_one
