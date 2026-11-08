"""Шлюзы Meta по уровню работы. Уровень читается при КАЖДОМ обращении, поэтому переключение
уровня (`LevelSwitch.confirm`) действует сразу, без перезапуска процесса.

  демо  — `MetaClient` поверх `FakeGraphAPI` (хранилище в базе);
  тест  — настоящий `MetaClient`: `validate_only=True` для проверочных запросов и второй, без него,
          для временных родителей «[ТЕСТ]» (включать им запрещает политика Исполнителя);
  боевой — настоящий `MetaClient`.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from target_ai.clock import Clock
from target_ai.config import Settings
from target_ai.fakemeta import FakeGraphAPI
from target_ai.meta import MetaClient
from target_ai.config.runtime import DEFAULT_LEVEL, SETTING_LEVEL

log = logging.getLogger(__name__)

DEMO_TOKEN = "demo-token"
TEST_TOKEN = "test-token"


class GatewayUnavailable(RuntimeError):
    """Нет токена Meta для уровня, которому он нужен."""


class LevelGateways:
    def __init__(self, db, settings: Settings, clock: Clock, fake: FakeGraphAPI, *,
                 transport: httpx.AsyncBaseTransport | None = None, sleeper=None) -> None:
        self._db, self._settings, self._clock, self._fake = db, settings, clock, fake
        self._transport, self._sleeper = transport, sleeper
        self._clients: dict[tuple[str, bool], MetaClient] = {}

    def __call__(self, validate_only: bool = False) -> MetaClient:
        level = self._db.settings.get(SETTING_LEVEL, DEFAULT_LEVEL)
        return self._client("fake" if level == "demo" else "real", validate_only)

    def real(self, validate_only: bool = False) -> MetaClient:
        """Настоящая Meta независимо от уровня: нужна проверке подключения."""
        return self._client("real", validate_only)

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

    def _client(self, mode: str, validate_only: bool) -> MetaClient:
        key = (mode, validate_only)
        if key not in self._clients:
            self._clients[key] = self._build(mode, validate_only)
        return self._clients[key]

    def _build(self, mode: str, validate_only: bool) -> MetaClient:
        common: dict[str, Any] = {"version": self._settings.meta_api_version, "validate_only": validate_only,
                                  "clock": self._clock, "model_name": self._settings.model_strong,
                                  "sleeper": self._sleeper}
        if mode == "fake":
            self._fake.seed_demo()
            return MetaClient(DEMO_TOKEN, transport=self._fake.transport, **common)
        token = self._settings.meta_access_token
        if token is None and self._transport is None:
            raise GatewayUnavailable("Токен Meta не задан: добавьте META_ACCESS_TOKEN в файл .env "
                                     "и перезапустите бота.")
        secret = self._settings.meta_app_secret
        return MetaClient(token.get_secret_value() if token else TEST_TOKEN, transport=self._transport,
                          app_secret=secret.get_secret_value() if secret else None,
                          app_id=self._settings.meta_app_id, **common)
