from target_ai.bot.menu import BTN_DAY, UNKNOWN_TEXT

from .conftest import OWNER, STRANGER


async def test_stranger_gets_only_access_denied_and_one_journal_line_per_hour(env):
    await env.tg.say(STRANGER, "/start")
    await env.tg.say(STRANGER, "/stop")

    assert env.tg.texts(STRANGER) == ["Доступ закрыт.", "Доступ закрыт."]
    assert env.db.settings.get("halted", False) is False  # чужая команда ничего не сделала
    denied = [e for e in env.db.audit.list() if e.action == "access_denied"]
    assert len(denied) == 1 and denied[0].data["telegram_id"] == STRANGER


async def test_stranger_button_press_is_refused_with_alert_and_nothing_is_executed(env):
    await env.tg.press(STRANGER, "sys:ok:whatever")

    assert [(a.text, a.show_alert) for a in env.tg.answers] == [("Доступ закрыт.", True)]
    assert env.tg.sent == []


async def test_start_shows_level_and_demo_buttons_only_in_demo(env):
    await env.tg.say(OWNER, "/start")
    demo_menu = env.tg.last(OWNER)
    env.db.settings.set("level", "test")
    await env.tg.say(OWNER, "/start", update_id=5000)
    test_menu = env.tg.last(OWNER)

    assert "Уровень работы: Демо" in demo_menu.text and BTN_DAY in sum(demo_menu.menu, [])
    assert "Уровень работы: Тест" in test_menu.text and BTN_DAY not in sum(test_menu.menu, [])
    assert "📋 Компании" in sum(test_menu.menu, []) and "🛑 Стоп-кран" in sum(test_menu.menu, [])


async def test_repeated_update_id_is_ignored(env):
    await env.tg.say(OWNER, "/companies", update_id=777)
    await env.tg.say(OWNER, "/companies", update_id=777)

    assert len(env.tg.to(OWNER)) == 1


async def test_unclear_text_gets_help_with_capabilities(env):
    await env.tg.say(OWNER, "абракадабра квадрат")

    assert env.tg.last(OWNER).text == UNKNOWN_TEXT


async def test_stop_button_sets_halted_and_resume_needs_one_time_confirmation(env):
    await env.tg.say(OWNER, "🛑 Стоп-кран")
    await env.tg.say(OWNER, "/resume_all")
    data = env.tg.last(OWNER).callbacks[0]
    await env.tg.press(OWNER, data)
    await env.tg.press(OWNER, data)  # двойное нажатие: Telegram мог доставить кнопку дважды

    assert env.db.settings.get("halted", False) is False
    assert "использовано или просрочено" in env.tg.last(OWNER).text
    assert sum(1 for e in env.db.audit.list() if e.action == "resume_system") == 1
