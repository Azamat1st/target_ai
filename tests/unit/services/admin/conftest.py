from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from target_ai.config import Settings
from target_ai.executor import Executor
from target_ai.fakemeta import FakeGraphAPI
from target_ai.meta import MetaClient
from target_ai.money import Money
from target_ai.notify import FakeNotifier
from target_ai.services.admin import ConnectionCheck
from target_ai.storage import Company, MetaObject

START = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ACCOUNT = "act_100000000000001"


class Clock:
    def now(self):
        return START


async def no_sleep(_seconds):
    return None


def make_settings(**overrides) -> Settings:
    values = {"telegram_bot_token": "bot", "meta_access_token": "meta-token", "anthropic_api_key": "sk-key",
              "meta_app_id": "1", "meta_app_secret": "secret", "telegram_allowed_ids": (5,), **overrides}
    return Settings(_env_file=None, **values)


@dataclass
class Env:
    db: object
    clock: Clock
    fake: FakeGraphAPI
    client: MetaClient
    notifier: FakeNotifier

    def settings(self, **overrides) -> Settings:
        return make_settings(**overrides)

    def checker(self, probe=None, ig_probe=None, **overrides) -> ConnectionCheck:
        return ConnectionCheck(self.settings(**overrides), lambda validate_only: self.client, self.clock, probe,
                               ig_probe or self.ig_probe)

    async def ig_probe(self, account_id, page_id, ig_user_id):
        """Как будущий `Executor.probe_ig_access`: пробное создание креатива в режиме проверки."""
        checker = MetaClient("meta-token", transport=self.fake.transport, clock=self.clock, sleeper=no_sleep,
                             validate_only=True)
        spec = {"page_id": page_id, "instagram_user_id": ig_user_id}
        await checker.create_creative(account_id, {"name": "[probe]", "object_story_spec": spec})

    def executor(self) -> Executor:
        return Executor(self.db, lambda validate_only: self.client, None, self.clock, self.notifier)

    def set_scopes(self, *scopes: str) -> None:
        self.fake.state.data["token"]["scopes"] = list(scopes)
        self.fake.state.save()

    def grant_all_scopes(self) -> None:
        from target_ai.services.admin.connection import OPTIONAL_SCOPES, REQUIRED_SCOPES
        self.set_scopes(*REQUIRED_SCOPES, *OPTIONAL_SCOPES)

    def company(self, *, name="Кофейня", demo=False, currency="USD") -> Company:
        return self.db.companies.create(Company(
            name=name, ad_account_id=ACCOUNT, currency=currency, timezone="UTC", mode="confirm",
            monthly_limit=Money(100_000, currency), profile={"demo": demo}, created_at=self.clock.now()))

    def own_campaign(self, company: Company, status="ACTIVE") -> str:
        obj = self.fake.state.add_object("campaign", ACCOUNT, {"name": "c"}, status=status)
        self.db.objects.create(MetaObject(company_id=company.id, meta_id=obj["id"], kind="campaign",
                                          name="c", status=status, created_at=self.clock.now()))
        return obj["id"]


@pytest.fixture
def env(db):
    clock = Clock()
    fake = FakeGraphAPI(store=db.fake_meta, clock=clock)
    fake.seed_demo()
    client = MetaClient("meta-token", transport=fake.transport, clock=clock, sleeper=no_sleep,
                        app_id="1", app_secret="secret")
    environment = Env(db, clock, fake, client, FakeNotifier())
    environment.grant_all_scopes()
    return environment
