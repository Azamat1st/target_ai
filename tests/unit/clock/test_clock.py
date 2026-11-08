from datetime import UTC, datetime, timedelta

from target_ai.clock import RealClock, SimClock, to_account_time


class MemoryStore:
    def __init__(self, offset=timedelta(0)):
        self.offset = offset

    def get_offset(self):
        return self.offset

    def set_offset(self, value):
        self.offset = value


class FixedClock:
    def __init__(self, moment):
        self.moment = moment

    def now(self):
        return self.moment


T0 = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def test_real_clock_returns_aware_utc():
    now = RealClock().now()
    assert now.tzinfo is not None and now.utcoffset() == timedelta(0)


def test_sim_clock_applies_offset_to_base_time():
    clock = SimClock(MemoryStore(), base=FixedClock(T0))
    clock.advance(timedelta(days=7))
    assert clock.now() == T0 + timedelta(days=7)


def test_sim_clock_offsets_accumulate():
    clock = SimClock(MemoryStore(), base=FixedClock(T0))
    clock.advance(timedelta(days=1))
    clock.advance(timedelta(hours=5))
    assert clock.now() == T0 + timedelta(days=1, hours=5)


def test_sim_clock_offset_survives_recreation():
    store = MemoryStore()
    SimClock(store, base=FixedClock(T0)).advance(timedelta(days=3))
    assert SimClock(store, base=FixedClock(T0)).now() == T0 + timedelta(days=3)


def test_sim_clock_rejects_negative_advance():
    import pytest

    with pytest.raises(ValueError):
        SimClock(MemoryStore(), base=FixedClock(T0)).advance(timedelta(days=-1))


def test_to_account_time_converts_utc_to_account_zone():
    # 22:00 UTC в Алматы (UTC+5 в 2026) — уже следующие сутки
    local = to_account_time(datetime(2026, 11, 8, 22, 0, tzinfo=UTC), "Asia/Almaty")
    assert (local.day, local.hour) == (9, 3)
