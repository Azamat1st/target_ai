"""Фейковый Graph API и симулятор статистики (спека §7): демо и тесты без сети."""

from target_ai.fakemeta import errors
from target_ai.fakemeta.api import FakeGraphAPI, RecordedRequest
from target_ai.fakemeta.errors import FakeError
from target_ai.fakemeta.simulator import Simulator

__all__ = ["FakeError", "FakeGraphAPI", "RecordedRequest", "Simulator", "errors"]
