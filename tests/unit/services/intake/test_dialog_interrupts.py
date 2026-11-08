"""R09/R11/R12/R18.1: выбор Instagram кнопкой, новая анкета поверх старой, вопросы с «Отмена» и явно другой запрос."""

from target_ai.llm import OfflineLLM
from target_ai.services.intake.extract import extract_fields

from .conftest import CHAT
from .test_planning import ready, script

TEXT = "добавь кофейню @coffee_x, Берлин, 1500 евро в месяц, нужны сообщения"


async def test_instagram_is_chosen_by_button_after_page_and_text_handle_is_only_a_hint(env):
    intake = env.intake(OfflineLLM())
    await intake.handle_text(CHAT, TEXT)
    await env.press(intake, "Демо Германия")
    await env.press(intake, "Studio Yoga Berlin")

    assert "Выберите Instagram-аккаунт" in env.last() and "@coffee_x" in env.last()
    assert "Instagram, связанный" in " ".join(env.labels())
    assert "Проверьте данные" not in env.last()
    await env.press(intake, "Instagram, связанный")
    assert "Проверьте данные" in env.last()


async def test_add_text_in_the_middle_of_a_form_starts_a_new_form_with_new_fields(env):
    intake = env.intake(OfflineLLM())
    await intake.handle_text(CHAT, "добавь пекарню @bakery_y")

    await intake.handle_text(CHAT, "добавь кофейню @coffee_x, Берлин, 1500 евро в месяц, нужны сообщения")

    assert "Прошлая анкета (@bakery_y) отменена" in env.all_text()
    assert env.db.dialogs.get(CHAT).data["handle"] == "coffee_x"


async def test_start_resets_unfinished_form(env):
    intake = env.intake(OfflineLLM())
    await intake.handle_text(CHAT, "добавь пекарню @bakery_y")

    await intake.reset(CHAT)

    assert env.db.dialogs.get(CHAT) is None


def test_site_without_scheme_is_recognised_and_handle_with_dot_is_not_a_site():
    assert extract_fields("кофейня coffee-x.de Берлин").website == "https://coffee-x.de"
    assert extract_fields("кофейня @coffee.de Берлин").website is None


async def test_question_has_cancel_button_that_drops_the_question(env):
    company, ids = ready(env, profile={"objective": "lead_form"})
    intake = env.intake(script(ids))
    await intake.planning.propose(company.id, CHAT)

    assert "Отмена" in " ".join(env.labels())
    await env.press(intake, "Отмена")

    assert env.db.dialogs.get(CHAT) is None and "Вопрос снят" in env.last()


async def test_report_request_releases_the_question_instead_of_complaining_about_the_link(env):
    company, ids = ready(env, profile={"objective": "lead_form"})
    intake = env.intake(OfflineLLM())
    await intake.planning.propose(company.id, CHAT)

    handled = await intake.handle_text(CHAT, "отчёт за неделю")

    assert handled is False  # дальше текст разбирает бот как «отчёт»
    assert env.db.dialogs.get(CHAT) is None and "не похоже на ссылку" not in env.all_text()


async def test_add_company_text_releases_the_question_and_starts_a_form(env):
    company, ids = ready(env, profile={"objective": "lead_form"})
    intake = env.intake(OfflineLLM())
    await intake.planning.propose(company.id, CHAT)

    await intake.handle_text(CHAT, TEXT)

    assert env.db.dialogs.get(CHAT).state == "onb"
