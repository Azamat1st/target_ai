from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from support.fake_telegram import FakeTelegram
from target_ai.app import App, AppConfigError
from target_ai.money import Money
from target_ai.notify.telegram import TELEGRAM_LIMIT, TelegramNotifier, escape, fit_chunks
from target_ai.storage import Company

from .conftest import OWNER


def make_company(env, **over) -> Company:
    fields = dict(name="Кофейня", instagram_handle="coffee_x", ad_account_id="act_100000000000002", currency="EUR",
                  timezone="Europe/Berlin", monthly_limit=Money(100000, "EUR"), mode="confirm", status="active", profile={"demo": True},
                  created_at=datetime(2026, 11, 8, 12, 0, tzinfo=UTC))
    return env.db.companies.create(Company(**{**fields, **over}))


async def test_questionnaire_from_start_to_summary_in_demo(env):
    await env.tg.say(OWNER, "/start")
    await env.tg.say(OWNER, "добавь кофейню @coffee_x, Берлин, 1500 евро в месяц, нужны сообщения")
    await env.tg.press_button(OWNER, "Демо Германия")
    await env.tg.press_button(OWNER, "Studio Yoga Berlin")
    await env.tg.press_button(OWNER, "Instagram, связанный")

    summary = env.tg.last(OWNER)
    assert "Всё верно" in " ".join(summary.labels)
    assert "Кофейня" in summary.text and "EUR" in summary.text or "€" in summary.text
    assert env.db.companies.list() == []  # пока не нажато «Всё верно», в базе ничего нет


async def test_add_button_starts_the_questionnaire(env):
    await env.tg.say(OWNER, "➕ Компания")

    assert env.tg.last(OWNER).text and env.db.dialogs.get(OWNER).state == "onb"


async def test_limit_from_text_changes_nothing_until_the_confirm_button_and_only_once(env):
    company = make_company(env)

    await env.tg.say(OWNER, "лимит 500 евро")
    data = env.tg.last(OWNER).callbacks[0]
    assert env.db.companies.get(company.id).monthly_limit == Money(100000, "EUR")
    await env.tg.press(OWNER, data)
    await env.tg.press(OWNER, data)

    assert env.db.companies.get(company.id).monthly_limit == Money(50000, "EUR")
    assert sum(1 for e in env.db.audit.list() if e.action == "set_monthly_limit") == 1


async def test_setting_value_is_asked_then_saved_and_journaled(env):
    await env.tg.say(OWNER, "⚙️ Настройки")
    edit = next(c for c in env.tg.last(OWNER).callbacks if c.startswith("set:edit:report_time"))
    await env.tg.press(OWNER, edit)
    await env.tg.say(OWNER, "09:30")

    assert env.db.settings.get("report_time") == "09:30"
    assert any(e.action == "setting_change" for e in env.db.audit.list())


async def test_oversized_video_is_not_downloaded_and_photo_reaches_media_service(env):
    await env.tg.send_video(OWNER, b"x", declared_size=25 * 1024 * 1024)
    await env.tg.send_photo(OWNER, b"png-bytes")

    downloads = [r for r in env.tg.session.requests if type(r).__name__ == "GetFile"]
    assert [d.file_id for d in downloads] == ["photo2"]  # видео 25 МБ не скачивается, фото — да
    assert len(env.tg.to(OWNER)) >= 2  # на оба файла сервис креативов ответил


async def test_long_text_is_split_below_telegram_limit_and_html_is_escaped(env):
    text = ("a < b & c > d " * 40 + "\n") * 40

    await env.app.notifier.send(OWNER, text, [[("Кнопка", "x:1")]])

    parts = env.tg.to(OWNER)
    assert len(parts) >= 2 and all(len(p.text) <= TELEGRAM_LIMIT for p in parts)
    assert "<" not in parts[0].text.replace("&lt;", "")
    assert parts[-1].buttons == [[("Кнопка", "x:1")]] and parts[0].buttons == []


def test_fit_chunks_keep_escaped_text_within_limit():
    chunks = fit_chunks("&" * 9000)

    assert all(len(escape(c)) <= TELEGRAM_LIMIT for c in chunks) and "".join(chunks) == "&" * 9000


async def test_rewind_button_sends_one_summary_to_owner_in_demo_and_is_refused_elsewhere(env):
    await env.tg.say(OWNER, "⏩ +1 день")
    summary = [t for t in env.tg.texts(OWNER) if t.startswith("⏩ Перемотка на 1")]
    env.db.settings.set("level", "test")
    await env.tg.say(OWNER, "/rewind 3")

    assert len(summary) == 1
    assert env.tg.last(OWNER).text == "Перемотка времени доступна только на демо-уровне."


async def test_switching_level_rebuilds_gateways_without_restart(make_env):
    env = make_env(meta_access_token=SecretStr("EAAtoken"))
    demo_client = env.app.gateways(False)
    await env.tg.say(OWNER, "/level test")
    test_validate, test_plain = env.app.gateways(True), env.app.gateways(False)

    assert env.db.settings.get("level") == "test"
    assert test_validate.validate_only and not test_plain.validate_only
    assert test_plain is not demo_client
    assert BTN_NOT_DEMO(env.tg.last(OWNER))


def BTN_NOT_DEMO(message) -> bool:
    return message.menu is not None and "⏩ +1 день" not in sum(message.menu, [])


async def test_startup_deletes_webhook_recovers_launches_and_greets_owner(env, monkeypatch):
    calls = []

    async def spy():
        calls.append("recover")
        return []

    monkeypatch.setattr(env.app.executor, "recover_incomplete", spy)
    await env.app.startup()
    await env.app._ticker.stop()

    assert env.tg.session.webhook_deleted == 1 and calls == ["recover"]
    assert "Бот запущен" in env.tg.last(OWNER).text


def test_app_requires_bot_token_and_rejects_unknown_overrides(tmp_path):
    from target_ai.config import Settings

    bare = Settings(_env_file=None, database_path=tmp_path / "a.sqlite3")
    with pytest.raises(AppConfigError, match="TELEGRAM_BOT_TOKEN"):
        App.create(bare)
    token = Settings(_env_file=None, telegram_bot_token=SecretStr("1:T"), database_path=tmp_path / "b.sqlite3")
    with pytest.raises(ValueError, match="surprise"):
        App.create(token, {"surprise": 1})


def test_main_exits_with_russian_hint_when_token_missing(monkeypatch, capsys, tmp_path):
    from target_ai import __main__ as entry
    from target_ai.config import Settings

    monkeypatch.setattr(entry, "load_settings", lambda: Settings(_env_file=None, database_path=tmp_path / "c.sqlite3"))

    code = entry.main()

    assert code != 0 and "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


async def test_connection_check_probes_real_meta_even_on_demo_level(make_env):
    from target_ai.fakemeta import FakeGraphAPI, errors

    real = FakeGraphAPI()
    real.seed_demo()
    real.inject_error(r"POST .*adcreatives", errors.ig_access_denied(), times=20)
    env = make_env({"meta_transport": real.transport}, meta_access_token=SecretStr("EAAtoken"))

    await env.tg.say(OWNER, "/check")

    line = next(t for t in env.tg.last(OWNER).text.split("\n") if "нет доступа к Instagram" in t)
    assert line.startswith("❌") and env.db.settings.get("level", "demo") == "demo"
    assert real.requests_matching(r"POST .*adcreatives")


async def test_leaving_demo_after_rewind_runs_token_check_on_next_tick(make_env):
    env = make_env(meta_access_token=SecretStr("EAAtoken"))
    make_company(env, timezone="UTC")
    env.app.scheduler._scheduler.ensure_jobs()
    await env.tg.say(OWNER, "/rewind 7")
    runs_before = env.db.jobs.list(kind="token_check")[0].last_run_at
    await env.tg.say(OWNER, "/level test")

    job = env.db.jobs.list(kind="token_check")[0]
    assert job.due_at <= env.app.clock.now()
    assert job.last_run_at == runs_before
    report = env.db.jobs.list(kind="daily_report")
    assert report and all(j.due_at.hour == 10 and 0 < (j.due_at - env.app.clock.now()).total_seconds() <= 86400
                          for j in report)
    runs = await env.app.scheduler.tick()
    assert "token_check" in [r.kind for r in runs]


async def test_denied_journal_uses_hourly_alert_mark_not_settings_keys(env):
    from .conftest import STRANGER

    await env.tg.say(STRANGER, "привет")

    assert not [k for k in env.db.settings.list() if k.startswith("access_denied")]
    assert any(e.action == "access_denied" for e in env.db.audit.list())


async def test_test_to_live_switch_leaves_scheduled_jobs_untouched(make_env):
    env = make_env(meta_access_token=SecretStr("EAAtoken"))
    await env.tg.say(OWNER, "/level test")
    env.app.scheduler._scheduler.ensure_jobs()
    before = {(j.kind, j.company_id): j.due_at for j in env.db.jobs.list()}
    env.db.settings.set("level", "test")

    reply = await env.app.levels.request("live")  # ждёт кнопку; часы не меняются
    await env.app.levels.confirm(reply.approval_id)

    assert {(j.kind, j.company_id): j.due_at for j in env.db.jobs.list()} == before


async def test_weekly_report_on_request_gives_weekly_review(env):
    make_company(env)

    await env.tg.say(OWNER, "отчёт за неделю")

    assert "Недельный разбор" in env.tg.last(OWNER).text


def test_main_with_rejected_bot_token_prints_russian_message_without_traceback(monkeypatch, capsys, tmp_path):
    from aiogram.exceptions import TelegramUnauthorizedError
    from aiogram.methods import GetMe
    from pydantic import SecretStr

    from target_ai import __main__ as entry
    from target_ai.config import Settings

    settings = Settings(_env_file=None, telegram_bot_token=SecretStr("123456:REVOKED"), telegram_allowed_ids=(OWNER,),
                        database_path=tmp_path / "c.sqlite3")
    monkeypatch.setattr(entry, "load_settings", lambda: settings)

    async def rejected(self):
        raise TelegramUnauthorizedError(GetMe(), "Unauthorized")
    monkeypatch.setattr(App, "run", rejected)

    code = entry.main()

    err = capsys.readouterr().err
    assert code != 0 and "Telegram не принял токен бота" in err and "TELEGRAM_BOT_TOKEN" in err
    assert "Traceback" not in err and "123456:REVOKED" not in err


def test_main_with_malformed_bot_token_prints_russian_message_without_traceback(monkeypatch, capsys, tmp_path):
    from pydantic import SecretStr

    from target_ai import __main__ as entry
    from target_ai.config import Settings

    settings = Settings(_env_file=None, telegram_bot_token=SecretStr("not-a-token"), telegram_allowed_ids=(OWNER,),
                        database_path=tmp_path / "c.sqlite3")
    monkeypatch.setattr(entry, "load_settings", lambda: settings)

    code = entry.main()

    err = capsys.readouterr().err
    assert code != 0 and "Telegram не принял токен бота" in err and "Traceback" not in err



async def test_run_with_rejected_token_closes_bot_session_before_exit(env, monkeypatch):
    from aiogram.exceptions import TelegramUnauthorizedError
    from aiogram.methods import DeleteWebhook

    closed = []

    async def close():
        closed.append(True)
    monkeypatch.setattr(env.tg.session, "close", close)
    env.tg.session.fail_next(DeleteWebhook, TelegramUnauthorizedError(DeleteWebhook(), "Unauthorized"))

    with pytest.raises(TelegramUnauthorizedError):
        await env.app.run()

    assert closed == [True]
