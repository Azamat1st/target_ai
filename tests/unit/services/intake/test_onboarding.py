from target_ai.llm import OfflineLLM
from target_ai.money import Money, format_ru

from .conftest import CHAT

TEXT = "добавь кофейню @coffee_x, Берлин, 1500 евро в месяц, нужны сообщения"


async def test_free_text_fills_fields_and_company_is_written_only_by_confirm_button(env):
    intake = env.intake(OfflineLLM())

    await intake.handle_text(CHAT, TEXT)
    await env.press(intake, "Демо Германия")
    await env.press(intake, "Studio Yoga Berlin")

    summary = env.last()
    assert format_ru(Money(150000, "EUR")) in summary
    assert "валюта вашего рекламного аккаунта" in summary
    assert "@coffee_x" in summary and "Берлин" in summary
    assert env.db.companies.list() == []
    await env.press(intake, "Всё верно")
    company = env.db.companies.list()[0]
    assert company.monthly_limit == Money(150000, "EUR")
    assert (company.currency, company.instagram_handle) == ("EUR", "coffee_x")
    assert company.ad_account_id == "act_100000000000002" and company.ig_user_id


async def test_second_press_of_confirm_does_not_create_second_company(env):
    intake = env.intake(OfflineLLM())
    await intake.handle_text(CHAT, TEXT)
    await env.press(intake, "Демо Германия")
    await env.press(intake, "Studio Yoga Berlin")
    data = env.callback("Всё верно")

    await intake.handle_callback(CHAT, data)
    await intake.handle_callback(CHAT, data)

    assert len(env.db.companies.list()) == 1
    assert "уже использована" in env.last()


async def test_missing_fields_are_asked_one_by_one_and_survive_restart(env):
    first = env.intake(OfflineLLM())
    await first.handle_text(CHAT, "добавь компанию @shop_x")
    assert "городе" in env.last()

    second = env.intake(OfflineLLM())  # «перезапуск»: новое приложение, та же база
    await second.handle_text(CHAT, "Берлин")

    assert "нужно от рекламы" in env.last()
    assert env.db.dialogs.get(CHAT).data["handle"] == "shop_x"


async def test_invalid_answers_are_explained_and_keep_entered_data(env):
    intake = env.intake(OfflineLLM())
    await intake.handle_text(CHAT, "добавь компанию")
    await intake.handle_text(CHAT, "my shop")
    assert "без пробелов" in env.last()
    await intake.handle_text(CHAT, "@shop_x")
    await intake.handle_text(CHAT, "Атлантида")
    assert "Не знаю такого места" in env.last()
    await intake.handle_text(CHAT, "Берлин")
    await intake.handle_text(CHAT, "сообщения")
    await intake.handle_text(CHAT, "много")

    assert "бюджет" in env.last().lower()
    data = env.db.dialogs.get(CHAT).data
    assert (data["handle"], data["countries"], data["objective"]) == ("shop_x", ["DE"], "direct_messages")


async def test_amount_in_other_currency_is_asked_again_in_account_currency_without_conversion(env):
    intake = env.intake(OfflineLLM())
    await intake.handle_text(CHAT, TEXT)
    await env.press(intake, "Демо США")
    await env.press(intake, "Кофейня Север")

    assert "USD" in env.last() and "Курс я не пересчитываю" in env.last()
    assert env.db.companies.list() == []
    await intake.handle_text(CHAT, "1000")
    assert format_ru(Money(100000, "USD")) in env.last()


async def test_no_accounts_explains_and_offers_connection_check(make_env):
    env = make_env(seed=False)
    intake = env.intake(OfflineLLM())

    await intake.handle_text(CHAT, TEXT)

    assert "не доступен ни один рекламный аккаунт" in env.last()
    assert await env.press(intake, "Проверить подключение")
    assert env.checked == [CHAT]


async def test_desired_result_price_is_extracted_shown_and_saved_without_extra_question(env):
    intake = env.intake(OfflineLLM())
    await intake.handle_text(CHAT, TEXT + ", цена сообщения до 3 евро")
    await env.press(intake, "Демо Германия")
    await env.press(intake, "Studio Yoga Berlin")

    assert format_ru(Money(300, "EUR")) in env.last() and "Желаемая цена результата" in env.last()
    assert format_ru(Money(150000, "EUR")) in env.last()  # бюджет не перепутан с ценой
    await env.press(intake, "Всё верно")
    assert env.db.companies.list()[0].target_cpa == Money(300, "EUR")


async def test_summary_says_city_is_shown_by_country_and_unknown_place_asks_for_country(env):
    intake = env.intake(OfflineLLM())
    await intake.handle_text(CHAT, TEXT)
    await env.press(intake, "Демо Германия")
    await env.press(intake, "Studio Yoga Berlin")
    assert "город Берлин → показ по стране Германия: точный радиус пока не поддерживается" in env.last()

    await env.press(intake, "Отмена")
    await intake.handle_text(CHAT, "добавь компанию @a_b")
    await intake.handle_text(CHAT, "Нижний Тагил")
    assert "Напишите страну показа" in env.last()
    await intake.handle_text(CHAT, "Германия")
    assert "нужно от рекламы" in env.last()


def test_place_names_match_only_known_endings():
    from target_ai.services.intake.geo import find_place
    assert find_place("кофейня Римма") is None and find_place("американский кофе") is None
    assert find_place("в Риме").name == "Рим" and find_place("США").is_country
