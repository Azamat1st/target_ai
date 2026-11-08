"""Оснастка сквозных тестов: всё приложение целиком, фейковый Telegram, фейковый Graph API, симулированные часы."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from support.fake_telegram import FakeTelegram
from target_ai.app import App
from target_ai.config import Settings
from target_ai.llm import OfflineLLM

OWNER = 111
STRANGER = 999
ADD_TEXT = "добавь кофейню @coffee_x, Берлин, 1500 евро в месяц, нужны сообщения, цена сообщения до 3 евро"


async def no_sleep(_seconds):
    return None


class E2E:
    """Приложение + «человек» в Telegram."""

    def __init__(self, tmp_path, overrides=None, **settings_over):
        self.settings = Settings(
            _env_file=None, telegram_bot_token=SecretStr("123456:TEST-token"), telegram_allowed_ids=(OWNER,),
            database_path=tmp_path / "data" / "e2e.sqlite3", **settings_over)
        self.tg = FakeTelegram()
        self.app = App.create(self.settings, overrides={
            "bot_session": self.tg.session, "llm": OfflineLLM(), "meta_sleeper": no_sleep, "tick_seconds": 3600,
            **(overrides or {})})
        self.tg.bind(self.app)

    @property
    def db(self):
        return self.app.db

    @property
    def fake(self):
        return self.app.fake_meta


@pytest.fixture
def make_e2e(tmp_path):
    made: list[E2E] = []

    def build(overrides=None, **settings_over) -> E2E:
        item = E2E(tmp_path / f"e{len(made)}", overrides, **settings_over)
        made.append(item)
        return item
    yield build
    for item in made:
        item.db.close()


CASES = {
    "EUR": ("добавь кофейню @coffee_x, Берлин, 1500 евро в месяц, нужны сообщения, цена сообщения до {cpa} евро",
            "Демо Германия", "Studio Yoga"),
    "USD": ("добавь кофейню @coffee_x, Нью-Йорк, 1500 долларов в месяц, нужны сообщения, цена сообщения до {cpa} долларов",
            "Демо США", "Кофейня Север"),
    "JPY": ("добавь кофейню @coffee_x, Токио, 150000 иен в месяц, нужны сообщения, цена сообщения до {cpa} иен",
            "Демо Япония", "Tokyo"),
}


async def reach_plan(e: E2E, currency: str = "EUR", cpa: int = 3, text: str | None = None) -> None:
    """Диалог «я -> бот» от «добавь компанию» до показанного медиаплана с кнопкой «Запустить»."""
    template, account, page = CASES[currency]
    await e.tg.say(OWNER, text or template.format(cpa=cpa))
    await e.tg.press_button(OWNER, account)
    await e.tg.press_button(OWNER, page)
    await e.tg.press_button(OWNER, "Instagram, связанный")
    await e.tg.press_button(OWNER, "Всё верно")
    await e.tg.press_button(OWNER, "Пропустить")
    await e.tg.press_button(OWNER, "Составить медиаплан")
    for _ in range(3):  # ЕС: получатель и плательщик рекламы
        if "🚀 Запустить" in e.tg.last(OWNER).labels:
            return
        await e.tg.say(OWNER, "Кофейня GmbH")
    assert "🚀 Запустить" in e.tg.last(OWNER).labels, e.tg.last(OWNER).text


def own_objects(e: E2E, kind: str) -> list[dict]:
    """Объекты фейкового Meta, созданные системой (с тегом [ta:…] в имени)."""
    return [o for o in e.fake.objects(kind) if "[ta:" in str(o["params"].get("name", ""))]
