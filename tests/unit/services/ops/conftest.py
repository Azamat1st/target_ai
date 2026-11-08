from datetime import timedelta

import pytest

from target_ai.clock import SimClock
from target_ai.executor import Executor
from target_ai.fakemeta import FakeGraphAPI, Simulator
from target_ai.llm import OfflineLLM
from target_ai.meta import MetaClient
from target_ai.money import Money
from target_ai.notify import FakeNotifier
from target_ai.scheduler import Scheduler
from target_ai.services.ops import DemoTime, Ops, OpsContext
from target_ai.storage import Company, MediaAsset, Plan

ACCOUNT = "act_100000000000001"


async def no_sleep(_seconds):
    return None


class World:
    def __init__(self, db, tmp_path):
        self.db, self.tmp_path = db, tmp_path
        self.clock = SimClock(db.sim)
        self.notifier = FakeNotifier()
        self.fake = FakeGraphAPI(store=db.fake_meta, clock=self.clock)
        self.fake.seed_demo()
        self.sim = Simulator(self.fake, self.clock)
        self.client = MetaClient("tok", transport=self.fake.transport, clock=self.clock, sleeper=no_sleep,
                                 app_id="1", app_secret="secret")
        self.validator = MetaClient("tok", transport=self.fake.transport, clock=self.clock, sleeper=no_sleep,
                                    validate_only=True)
        factory = lambda validate_only: self.validator if validate_only else self.client  # noqa: E731
        self.executor = Executor(db, factory, None, self.clock, self.notifier)
        self.ctx = OpsContext(db, self.clock, self.executor, factory, self.notifier, OfflineLLM())
        self.ops = Ops(self.ctx)
        self.scheduler = Scheduler(db, self.clock, self.ops.handlers(), self.notifier)
        self.demo = DemoTime(self.ctx, self.scheduler, self.sim, self.ops.reporting)

    def company(self, *, mode="confirm", groups=4, new_creative=False, target_cpa=250, limit_major=1000, name="Кофейня"):
        page = next(p for p in self.fake.state.data["pages"].values() if p["account_id"] == ACCOUNT)
        ig = page["instagram_business_account"]["id"]
        company = self.db.companies.create(Company(
            name=name, ad_account_id=ACCOUNT, page_id=page["id"], ig_user_id=ig, currency="USD",
            timezone="America/New_York", mode=mode, monthly_limit=Money(limit_major * 100, "USD"),
            target_cpa=Money(target_cpa, "USD"), profile={"demo": True}, created_at=self.clock.now()))
        self.plan(company, groups, new_creative)
        return company

    def plan(self, company, groups, new_creative):
        if new_creative:
            path = self.tmp_path / "pic.jpg"
            path.write_bytes(b"jpegdata")
            asset = self.db.media.create(MediaAsset(company_id=company.id, kind="image", path=str(path)))
            ads = [{"kind": "new_creative", "creative_id": str(asset.id), "text": f"Текст {i}", "headline": f"Заг {i}"}
                   for i in range(2)]
        else:
            posts = [m["id"] for m in self.fake.state.data["media"].values() if m["ig_id"] == company.ig_user_id]
            ads = [{"kind": "existing_post", "creative_id": posts[i]} for i in range(2)]
        money = {"amount_minor": 1000, "currency": "USD"}
        data = {"name": "Осенняя", "objective": "direct_messages", "groups": [
            {"name": f"Группа {n}", "hypothesis": "broad", "daily_budget": money, "goal_kind": "high_freq",
             "advantage_audience": 1, "countries": ["US"], "ads": ads} for n in range(1, groups + 1)]}
        self.db.plans.create(Plan(company_id=company.id, version=1, status="approved", data=data,
                                  created_at=self.clock.now(), updated_at=self.clock.now()))

    async def launch(self, company):
        plan = self.db.plans.latest(company.id)
        check = await self.executor.check_launch(plan.id)
        approval = self.executor.create_approval(check.needs_approval, company_id=company.id).id if check.needs_approval else None
        return await self.executor.launch(plan.id, approval)

    async def run_days(self, days):
        for _ in range(days):
            self.sim.simulate_day(self.clock.now().date())
            self.clock.advance(timedelta(days=1))

    def ads(self, company):
        return self.db.objects.list(company_id=company.id, kind="ad")


    def seed(self, company, meta_id, *, days, spend, results, impressions=0, frequency=0.0, back=1):
        """Строки статистики за `days` дней, оканчивая `back` дней назад (даты аккаунта)."""
        from target_ai.storage import DailyStat
        today = self.ctx.account_today(company)
        for offset in range(back, back + days):
            self.db.stats.upsert(DailyStat(
                meta_id=meta_id, company_id=company.id, date=(today - timedelta(days=offset)).isoformat(),
                spend=Money(spend, "USD"), impressions=impressions, results=results, frequency=frequency))

    def adsets(self, company):
        return self.db.objects.list(company_id=company.id, kind="adset")

    def budget(self, meta_id):
        return self.fake.object(meta_id)["daily_budget"]


@pytest.fixture
def world(db, tmp_path):
    return World(db, tmp_path)

