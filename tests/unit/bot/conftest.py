import pytest
from pydantic import SecretStr

from support.clock import FixedClock
from support.fake_telegram import FakeTelegram
from target_ai.app import App
from target_ai.config import Settings
from target_ai.llm import OfflineLLM

OWNER = 111
STRANGER = 999


async def _no_sleep(_seconds):
    return None


class Env:
    def __init__(self, tmp_path, overrides=None, **settings_over):
        self.settings = Settings(
            _env_file=None, telegram_bot_token=SecretStr("123456:TEST-token"), telegram_allowed_ids=(OWNER,),
            database_path=tmp_path / "data" / "t.sqlite3", **settings_over)
        self.tg = FakeTelegram()
        self.app = App.create(self.settings, overrides={
            "bot_session": self.tg.session, "llm": OfflineLLM(), "meta_sleeper": _no_sleep, "tick_seconds": 3600,
            "base_clock": FixedClock(), **(overrides or {})})
        self.tg.bind(self.app)

    @property
    def db(self):
        return self.app.db


@pytest.fixture
def make_env(tmp_path):
    made = []

    def build(overrides=None, **settings_over) -> Env:
        env = Env(tmp_path, overrides, **settings_over)
        made.append(env)
        return env
    yield build
    for env in made:
        env.app.db.close()


@pytest.fixture
def env(make_env):
    return make_env()
