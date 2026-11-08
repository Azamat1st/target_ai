from datetime import UTC, datetime, timedelta

import pytest

from target_ai.fakemeta import FakeGraphAPI
from target_ai.meta import MetaClient
from target_ai.money import Money

START = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


class StepClock:
    """Время, которое двигает только «сон»: реальных пауз в тестах нет."""

    def __init__(self) -> None:
        self.current = START

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class RecordingSleeper:
    def __init__(self, clock: StepClock) -> None:
        self._clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._clock.advance(timedelta(seconds=seconds))


@pytest.fixture
def clock():
    return StepClock()


@pytest.fixture
def sleeper(clock):
    return RecordingSleeper(clock)


@pytest.fixture
def fake(clock):
    api = FakeGraphAPI(clock=clock)
    api.seed_demo()
    return api


@pytest.fixture
def make_client(fake, clock, sleeper):
    def factory(**overrides):
        options = {"token": "tok-secret", "transport": fake.transport, "clock": clock, "sleeper": sleeper}
        return MetaClient(**{**options, **overrides})

    return factory


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def acct(fake):
    """Рекламные аккаунты демо по валюте: acct["USD"] -> "act_..."."""
    return {a["currency"]: a["id"] for a in fake.state.data["accounts"].values()}


@pytest.fixture
def build_tree(fake):
    """Создаёт кампанию -> группу -> креатив -> объявление через клиент; возвращает id."""

    async def build(client, account_id, *, budget_minor=2000, status="ACTIVE", goal="LINK_CLICKS", spend_cap_minor=None):
        currency = fake.state.data["accounts"][account_id]["currency"]
        page = next(iter(fake.state.data["pages"].values()))
        campaign = await client.create_campaign(
            account_id,
            {"name": "[ta:L1:c] Кампания", "objective": "OUTCOME_TRAFFIC", "special_ad_categories": [], "status": "PAUSED",
             **({"spend_cap": Money(spend_cap_minor, currency)} if spend_cap_minor else {})},
        )
        targeting = {
            "publisher_platforms": ["instagram"], "instagram_positions": ["stream", "story"],
            "device_platforms": ["mobile"], "geo_locations": {"countries": ["US"]},
            "targeting_automation": {"advantage_audience": 1},
        }
        adset = await client.create_adset(account_id, {
            "name": "[ta:L1:g] Группа", "campaign_id": campaign, "daily_budget": Money(budget_minor, currency),
            "optimization_goal": goal, "billing_event": "IMPRESSIONS", "targeting": targeting, "status": status,
        })
        creative = await client.create_creative(account_id, {
            "name": "[ta:L1:k] Креатив", "object_story_spec": {"page_id": page["id"], "link_data": {"link": "https://x.example"}},
        })
        ad = await client.create_ad(account_id, {
            "name": "[ta:L1:a] Объявление", "adset_id": adset, "creative": {"creative_id": creative}, "status": status,
        })
        if status == "ACTIVE":
            await client.set_status(campaign, "ACTIVE")
        return {"campaign": campaign, "adset": adset, "creative": creative, "ad": ad}

    return build
