"""Settings: единственный источник секретов и параметров запуска."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Секреты хранятся как SecretStr: repr/str значений не показывают."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
    )

    telegram_bot_token: SecretStr | None = None
    telegram_allowed_ids: Annotated[tuple[int, ...], NoDecode] = ()
    anthropic_api_key: SecretStr | None = None
    model_strong: str = "claude-opus-5-5"
    model_cheap: str = "claude-haiku-5-5"
    meta_access_token: SecretStr | None = None
    meta_app_id: str | None = None
    meta_app_secret: SecretStr | None = None
    meta_api_version: str = "v27.0"
    database_path: Path = Field(default=Path("data/target_ai.sqlite3"))
    report_tz: str = "UTC"
    log_level: str = "INFO"

    @field_validator("telegram_allowed_ids", mode="before")
    @classmethod
    def _parse_ids(cls, value: object) -> object:
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",") if p.strip()]
            try:
                return tuple(int(p) for p in parts)
            except ValueError as exc:
                raise ValueError(
                    "TELEGRAM_ALLOWED_IDS: ожидается список целых чисел через запятую."
                ) from exc
        return value


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Читает настройки; значения из окружения процесса важнее файла."""
    return Settings(_env_file=env_file)  # type: ignore[call-arg]
