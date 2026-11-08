from datetime import UTC, datetime, timedelta

import pytest

from target_ai.agents import AdCopySet, AdVariant, Objective, Passport, PassportFact, Voice
from target_ai.agents.schemas import AdVerdict, ChecklistItem, ComplianceVerdict, GroupProposal, Hypothesis, MediaPlanProposal, Segment
from target_ai.executor import Executor
from target_ai.fakemeta import FakeGraphAPI
from target_ai.meta import MetaClient
from target_ai.money import Money
from target_ai.notify import FakeNotifier
from target_ai.services.intake import Intake
from target_ai.storage import Company, MediaAsset
from target_ai.storage import Passport as PassportRow

CHAT = 1
START = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ACCOUNTS = {"USD": "act_100000000000001", "EUR": "act_100000000000002"}


class StepClock:
    def __init__(self):
        self.current = START

    def now(self):
        return self.current

    def advance(self, delta: timedelta):
        self.current += delta


async def _no_sleep(_seconds):
    return None


def make_passport(**over) -> Passport:
    fields = dict(
        offer="Кофейня: кофе и десерты", segments=(Segment(name="Офисные работники"), Segment(name="Студенты")),
        recommended_objective=Objective.DIRECT_MESSAGES, voice=Voice(language="ru", address="вы", style="тёплый"),
        facts=(PassportFact(text="Работаем с 8 до 20", source="сайт"),),
    )
    return Passport(**{**fields, **over})


def ok_verdict(count: int = 2) -> ComplianceVerdict:
    checks = tuple(ChecklistItem(rule=n, ok=True) for n in range(1, 10))
    return ComplianceVerdict(ads=tuple(AdVerdict(ad_index=i, checks=checks) for i in range(count)))


def copy_set(ids, texts=("Кофе и десерты каждый день", "Заходите на кофе")) -> AdCopySet:
    return AdCopySet(variants=tuple(
        AdVariant(kind="new_creative", creative_id=str(i), primary_text=t, headline="Кофейня", cta="INSTAGRAM_MESSAGE")
        for i, t in zip(ids, texts, strict=True)))


def proposal() -> MediaPlanProposal:
    return MediaPlanProposal(objective=Objective.DIRECT_MESSAGES, groups=(
        GroupProposal(name="Широкая", hypothesis=Hypothesis.BROAD, budget_share="0.5", locations=("DE",)),
        GroupProposal(name="Интересы", hypothesis=Hypothesis.INTERESTS, budget_share="0.5",
                      interest_keywords=("Кофе",), locations=("DE",)),
    ))


class Env:
    def __init__(self, db, tmp_path, *, level="live", seed=True):
        self.db, self.tmp_path = db, tmp_path
        self.clock, self.notifier = StepClock(), FakeNotifier()
        self.fake = FakeGraphAPI(store=db.fake_meta, clock=self.clock)
        if seed:
            self.fake.seed_demo()
        self.client = MetaClient("tok", transport=self.fake.transport, clock=self.clock, sleeper=_no_sleep)
        self.factory = lambda validate_only: self.client
        db.settings.set("level", level)
        self.executor = Executor(db, self.factory, None, self.clock, self.notifier)
        self.checked: list[int] = []

    def intake(self, llm) -> Intake:
        async def hook(chat_id):
            self.checked.append(chat_id)
        return Intake(self.db, self.factory, llm, self.clock, self.notifier, self.executor,
                      media_dir=self.tmp_path / "media", check_connection=hook)

    def last(self) -> str:
        return self.notifier.messages[-1].text

    def all_text(self) -> str:
        return "\n".join(self.notifier.texts)

    def labels(self) -> list[str]:
        return [label for m in self.notifier.messages[-3:] for row in m.buttons for label, _ in row]

    async def press(self, intake, label_part: str) -> bool:
        for message in reversed(self.notifier.messages):
            for row in message.buttons:
                for label, data in row:
                    if label_part in label:
                        return await intake.handle_callback(CHAT, data)
        raise AssertionError(f"нет кнопки «{label_part}»")

    def callback(self, label_part: str) -> str:
        for message in reversed(self.notifier.messages):
            for row in message.buttons:
                for label, data in row:
                    if label_part in label:
                        return data
        raise AssertionError(f"нет кнопки «{label_part}»")

    def company(self, currency="EUR", *, profile=None, mode="confirm", limit_major=1000) -> Company:
        account = ACCOUNTS[currency]
        page = next(p for p in self.fake.state.data["pages"].values() if p["account_id"] == account)
        base = {"countries": ["DE"], "geo": "Берлин", "objective": "direct_messages", "demo": False,
                "dsa": {"beneficiary": "Кофейня", "payor": "Кофейня"}}
        return self.db.companies.create(Company(
            name="Кофейня", instagram_handle="coffee_x", ad_account_id=account, page_id=page["id"],
            ig_user_id=page["instagram_business_account"]["id"], currency=currency, timezone="Europe/Berlin",
            monthly_limit=Money(limit_major * 100, currency), mode=mode, status="draft",
            profile={**base, **(profile or {})}, created_at=self.clock.now()))

    def with_passport(self, company: Company, passport: Passport | None = None) -> PassportRow:
        return self.db.passports.create(PassportRow(company_id=company.id, version=1,
            data=(passport or make_passport()).model_dump(mode="json"), created_at=self.clock.now()))

    def image(self, company: Company) -> MediaAsset:
        path = self.tmp_path / f"pic{len(self.db.media.list())}.jpg"
        path.write_bytes(b"jpeg")
        return self.db.media.create(MediaAsset(company_id=company.id, kind="image", path=str(path), created_at=self.clock.now()))


@pytest.fixture
def make_env(db, tmp_path):
    def build(**kwargs) -> Env:
        return Env(db, tmp_path, **kwargs)
    return build


@pytest.fixture
def env(make_env):
    return make_env()
