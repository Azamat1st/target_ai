from datetime import UTC, datetime, timedelta

import pytest

from target_ai.executor import Executor
from target_ai.fakemeta import FakeGraphAPI, Simulator
from target_ai.meta import MetaClient
from target_ai.money import Money
from target_ai.storage import Company, MediaAsset, Plan

START = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ACCOUNTS = {"USD": "act_100000000000001", "EUR": "act_100000000000002"}
USD = "USD"


class StepClock:
    def __init__(self):
        self.current = START

    def now(self):
        return self.current

    def advance(self, delta: timedelta):
        self.current += delta


async def no_sleep(_seconds):
    return None


class Notifier:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text, buttons=None):
        self.sent.append((text, buttons))
        return len(self.sent)


class Flaky:
    """Обёртка шлюза: сбой на n-м создании объявления / потеря ответа на создании группы."""

    def __init__(self, inner, *, fail_ad_at=None, lose_adset_response=False):
        self._inner, self.fail_ad_at, self.lose_adset = inner, fail_ad_at, lose_adset_response
        self.ad_calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def create_ad(self, account_id, params, *, idempotency_key=None):
        self.ad_calls += 1
        if self.ad_calls == self.fail_ad_at:
            raise RuntimeError("сбой сети")
        return await self._inner.create_ad(account_id, params, idempotency_key=idempotency_key)

    async def create_adset(self, account_id, params, *, idempotency_key=None):
        created = await self._inner.create_adset(account_id, params, idempotency_key=idempotency_key)
        if self.lose_adset:
            raise RuntimeError("ответ потерян")
        return created


class Env:
    def __init__(self, db, tmp_path):
        self.db, self.tmp_path = db, tmp_path
        self.clock, self.notifier = StepClock(), Notifier()
        self.fake = FakeGraphAPI(store=db.fake_meta, clock=self.clock)
        self.fake.seed_demo()
        self.sim = Simulator(self.fake, self.clock)
        self.client = MetaClient("tok", transport=self.fake.transport, clock=self.clock, sleeper=no_sleep)
        self.validator = MetaClient("tok", transport=self.fake.transport, clock=self.clock, sleeper=no_sleep,
                                    validate_only=True)
        db.settings.set("level", "live")

    @staticmethod
    def flaky(**kw):
        return lambda client: Flaky(client, **kw)

    def executor(self, wrap=None):
        real = wrap(self.client) if wrap else self.client
        return Executor(self.db, lambda validate_only: self.validator if validate_only else real, None,
                        self.clock, self.notifier)

    def company(self, currency=USD, *, mode="confirm", limit_major=1000, name="Кофейня"):
        account = ACCOUNTS[currency]
        page = next(p for p in self.fake.state.data["pages"].values() if p["account_id"] == account)
        ig = page["instagram_business_account"]["id"]
        return self.db.companies.create(Company(
            name=name, ad_account_id=account, page_id=page["id"], ig_user_id=ig, currency=currency,
            timezone="America/New_York", mode=mode, monthly_limit=Money(limit_major * 100, currency),
            created_at=self.clock.now()))

    def media_ids(self, company):
        return [m["id"] for m in self.fake.state.data["media"].values() if m["ig_id"] == company.ig_user_id]

    def plan(self, company, *, version=1, budget=1000, new_creative=None, status="approved"):
        posts = self.media_ids(company)
        kinds = [{"kind": "existing_post", "creative_id": posts[i]} for i in range(2)] if new_creative is None else [
            {"kind": "new_creative", "creative_id": str(new_creative), "text": f"Текст {i}", "headline": f"Заголовок {i}"}
            for i in range(2)]
        money = {"amount_minor": budget, "currency": company.currency}
        groups = [{"name": n, "hypothesis": "broad", "daily_budget": money, "goal_kind": "high_freq",
                   "advantage_audience": 1, "countries": ["US"], "ads": kinds} for n in ("Широкая 1", "Широкая 2")]
        data = {"name": "Осенняя", "objective": "direct_messages", "groups": groups}
        return self.db.plans.create(Plan(company_id=company.id, version=version, status=status, data=data,
                                         created_at=self.clock.now(), updated_at=self.clock.now()))

    def media_asset(self, company):
        path = self.tmp_path / "pic.jpg"
        path.write_bytes(b"jpegdata")
        return self.db.media.create(MediaAsset(company_id=company.id, kind="image", path=str(path)))

    async def launch(self, ex, plan):
        check = await ex.check_launch(plan.id)
        approval = None
        if check.needs_approval:
            approval = ex.create_approval(check.needs_approval, company_id=plan.company_id).id
        return await ex.launch(plan.id, approval)

    def own_ids(self, company):
        return [o.meta_id for o in self.db.objects.list(company_id=company.id)]

    def live(self, kind, account=ACCOUNTS[USD]):
        return [o for o in self.fake.objects(kind, account) if not o.get("foreign")]

    def mutations(self):
        return [r for r in self.fake.requests if r.method != "GET"]


@pytest.fixture
def env(db, tmp_path):
    return Env(db, tmp_path)
