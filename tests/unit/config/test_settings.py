from pathlib import Path

import pytest

from target_ai.config import Settings, load_settings

ENV_NAMES = [
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_IDS", "ANTHROPIC_API_KEY", "MODEL_STRONG", "MODEL_CHEAP",
    "META_ACCESS_TOKEN", "META_APP_ID", "META_APP_SECRET", "META_API_VERSION", "DATABASE_PATH",
    "REPORT_TZ", "LOG_LEVEL",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def write_env(tmp_path: Path, body: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_values_from_env_file(tmp_path):
    env = write_env(tmp_path, "TELEGRAM_ALLOWED_IDS=111, 222,333\nMETA_API_VERSION=v28.0\nLOG_LEVEL=DEBUG\n")
    s = load_settings(env)
    assert s.telegram_allowed_ids == (111, 222, 333)
    assert s.meta_api_version == "v28.0"
    assert s.log_level == "DEBUG"


def test_missing_optional_values_do_not_break_loading(tmp_path):
    s = load_settings(tmp_path / "absent.env")
    assert s.anthropic_api_key is None
    assert s.meta_access_token is None
    assert s.telegram_bot_token is None
    assert s.telegram_allowed_ids == ()


def test_empty_values_in_env_file_are_treated_as_unset(tmp_path):
    env = write_env(tmp_path, "ANTHROPIC_API_KEY=\nTELEGRAM_ALLOWED_IDS=\nMODEL_STRONG=\n")
    s = load_settings(env)
    assert s.anthropic_api_key is None
    assert s.telegram_allowed_ids == ()
    assert s.model_strong == "claude-opus-5-5"


def test_defaults_match_spec(tmp_path):
    s = load_settings(tmp_path / "absent.env")
    assert (s.model_strong, s.model_cheap) == ("claude-opus-5-5", "claude-haiku-5-5")
    assert s.meta_api_version == "v27.0"
    assert s.database_path == Path("data/target_ai.sqlite3")


def test_process_environment_overrides_file(tmp_path, monkeypatch):
    env = write_env(tmp_path, "LOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    assert load_settings(env).log_level == "WARNING"


def test_non_numeric_allowed_ids_fail_loudly(tmp_path):
    env = write_env(tmp_path, "TELEGRAM_ALLOWED_IDS=12,abc\n")
    with pytest.raises(ValueError):
        load_settings(env)


def test_repr_and_str_hide_secret_values(tmp_path):
    secrets = {
        "TELEGRAM_BOT_TOKEN": "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "ANTHROPIC_API_KEY": "sk-ant-api03-supersecretvalue",
        "META_ACCESS_TOKEN": "EAAGsupersecrettoken123",
        "META_APP_SECRET": "appsecretvalue999",
    }
    env = write_env(tmp_path, "\n".join(f"{k}={v}" for k, v in secrets.items()))
    s = load_settings(env)
    shown = repr(s) + str(s)
    for value in secrets.values():
        assert value not in shown
    assert s.anthropic_api_key.get_secret_value() == secrets["ANTHROPIC_API_KEY"]


def test_settings_are_immutable(tmp_path):
    s = load_settings(tmp_path / "absent.env")
    assert isinstance(s, Settings)
    with pytest.raises(ValueError):
        s.log_level = "DEBUG"  # type: ignore[misc]
