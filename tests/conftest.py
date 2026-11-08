from datetime import UTC, datetime

import pytest

from target_ai.storage import Database

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    yield database
    database.close()
