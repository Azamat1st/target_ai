"""Атаки на деньги из ревью: каждый сценарий воспроизводит найденный обход."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from target_ai.agents.schemas import (
    AdCopySet, AdVariant, AdVerdict, ChecklistItem, ComplianceVerdict, GroupProposal, Hypothesis,
    MediaPlanProposal, Objective, SpecialCategory, Violation,
)
from target_ai.money import CurrencyMismatch, Money
from target_ai.policy import (
    Activate, AdInput, AdState, Allowed, CampaignState, CompanySnapshot, CreateObject, Denied,
    GroupInput, GroupState, InterestInfo, NeedsApproval, OptimizerDecision, PlanContext,
    PlanProposalInput, SetDailyBudget, SetSpendCap, action_key, check_action, check_decisions,
    decide, validate_plan,
)

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


def usd(minor: int) -> Money:
    return Money(minor, "USD")


def grp(gid, budget=1_000, *, active=True, spend=None, results=0, spent_today=None, ads=()):
    return GroupState(
        id=gid, name=gid, campaign_id="c1", daily_budget=usd(budget), active=active,
        spend=usd(spend) if spend is not None else None, results=results,
        spent_today=usd(spent_today) if spent_today is not None else None,
        active_since=NOW - timedelta(hours=96), last_budget_change_at=NOW - timedelta(hours=72),
        ads=tuple(ads))


def snap(groups, **over) -> CompanySnapshot:
    groups = tuple(groups)
    base = dict(
        company_id=1, currency="USD", timezone="UTC", now=NOW, mode="confirm", level="live",
        is_demo=False, halted=False, monthly_limit=usd(100_000), spent_month=usd(40_000),
        min_budget_imp=usd(200), min_budget_high_freq=usd(500), min_spend_cap=usd(10_000),
        groups=groups,
        campaigns=(CampaignState(id="c1", spent=usd(10_000), spend_cap=usd(50_000)),),
        own_object_ids=frozenset({"c1"} | {g.id for g in groups} | {a.id for g in groups for a in g.ads}),
    )
    return CompanySnapshot(**{**base, **over})


def kinds(verdicts) -> list[str]:
    return [type(v).__name__ for _, v in verdicts]


# --- 1. пакет решений: отклонённое не применяется -------------------------------------------

def test_denied_cut_does_not_turn_a_raise_into_a_transfer():
    a = grp("A", 1_500, spend=5_000, results=10)                          # сильная
    b = grp("B", 1_500, spend=3_000, results=2, spent_today=1_400)       # слабая, снизить нельзя
    snapshot = snap([a, b], spent_month=usd(52_000))                       # потолок 30 $
    decisions = decide(snapshot, target_cpa=usd(1_000))
    verdicts = dict((d.target_id, v) for d, v in check_decisions(decisions, snapshot))
    assert isinstance(verdicts["B"], Denied) and verdicts["B"].code == "below_spent"
    assert isinstance(verdicts["A"], Denied) and verdicts["A"].code == "over_cap"


def test_raise_above_start_sum_without_matching_cut_needs_button_when_cap_allows():
    a, b = grp("A", 1_000), grp("B", 1_000)
    batch = [OptimizerDecision("set_budget", "A", usd(1_200), "перенос")]
    ((_, verdict),) = check_decisions(batch, snap([a, b]))
    assert isinstance(verdict, NeedsApproval)


def test_transfer_with_accepted_cut_goes_through_without_button():
    a, b = grp("A", 1_000), grp("B", 1_000)
    batch = [OptimizerDecision("set_budget", "A", usd(1_200), ""),
             OptimizerDecision("set_budget", "B", usd(800), "")]
    assert kinds(check_decisions(batch, snap([a, b]))) == ["Allowed", "Allowed"]


def test_denied_raise_then_pause_does_not_free_phantom_budget_for_other_group():
    a1, a2 = grp("A1", 1_000), grp("A2", 1_000)
    batch = [OptimizerDecision("set_budget", "A1", usd(5_000), ""),   # +400 %: отклонено
             OptimizerDecision("pause_group", "A1", None, ""),
             OptimizerDecision("set_budget", "A2", usd(1_200), "")]
    verdicts = {d.target_id + d.kind: v for d, v in check_decisions(batch, snap([a1, a2]))}
    assert isinstance(verdicts["A1set_budget"], Denied)
    assert verdicts["A1pause_group"] == Allowed()
    assert verdicts["A2set_budget"] == Allowed()  # пауза A1 настоящая: сумма не выше исходной


# --- 6. пауза в пакете всегда проходит -------------------------------------------------------

def test_scale_followed_by_pause_does_not_crash_and_pause_is_allowed():
    ad = AdState(id="x")
    a1, a2 = grp("A1", 1_000, ads=(ad,)), grp("A2", 1_000)
    snapshot = snap([a1, a2], mode="autonomous")
    batch = [OptimizerDecision("scale", "A1", usd(1_200), ""), OptimizerDecision("pause_ad", "x", None, ""),
             OptimizerDecision("pause_group", "A2", None, "")]
    verdicts = check_decisions(batch, snapshot)
    assert [type(v) for _, v in verdicts] == [Allowed, Allowed, Allowed]
    confirm = check_decisions(batch, replace(snapshot, mode="confirm"))
    assert [d.kind for d, v in confirm if isinstance(v, Allowed)] == ["pause_ad", "pause_group"]


# --- 11. подтверждение привязано к действию ---------------------------------------------------

def test_one_approval_does_not_cover_other_scalings_of_the_batch():
    a1, a2 = grp("A1", 1_000), grp("A2", 1_000)
    snapshot = snap([a1, a2])
    batch = [OptimizerDecision("scale", "A1", usd(1_200), ""), OptimizerDecision("scale", "A2", usd(1_200), "")]
    approved = frozenset({action_key(batch[0].to_action(), snapshot)})
    assert kinds(check_decisions(batch, snapshot, approved_keys=approved)) == ["Allowed", "NeedsApproval"]
    assert kinds(check_decisions(batch, snapshot)) == ["NeedsApproval", "NeedsApproval"]


def test_approval_for_one_amount_does_not_cover_a_bigger_one():
    snapshot = snap([grp("A1", 1_000)])
    ok, bigger = SetDailyBudget("A1", usd(1_100)), SetDailyBudget("A1", usd(1_200))
    keys = frozenset({action_key(ok, snapshot)})
    assert check_action(ok, snapshot, approved_keys=keys) == Allowed()
    assert isinstance(check_action(bigger, snapshot, approved_keys=keys), NeedsApproval)


# --- 2, 8. включение: вид объекта сверяется со снимком ----------------------------------------

def test_group_cannot_be_activated_as_an_ad_and_exhausted_month_blocks_any_activation():
    paused = grp("gP", 2_000, active=False)
    live = grp("gL", 1_000, ads=(AdState(id="ad1", active=False),))
    for mode in ("confirm", "autonomous"):
        s = snap([paused, live], mode=mode)
        mismatch = check_action(Activate("gP", "ad"), s)
        assert isinstance(mismatch, Denied) and mismatch.code == "unknown_object"
        exhausted = snap([paused, live], mode=mode, spent_month=usd(100_000))
        for act in (Activate("gP", "group"), Activate("gP", "ad"), Activate("ad1", "ad"),
                    Activate("c1", "campaign")):
            verdict = check_action(act, exhausted)
            assert isinstance(verdict, Denied) and verdict.code in {"month_exhausted", "unknown_object"}, act
        real = check_action(Activate("ad1", "ad"), exhausted)
        assert real.code == "month_exhausted"


def test_campaign_activation_counts_its_paused_groups_and_respects_cap():
    s = snap([grp("g1", 2_000, active=False), grp("g2", 2_000, active=False)])  # потолок 37,50 $
    verdict = check_action(Activate("c1", "campaign"), s)
    assert isinstance(verdict, Denied) and verdict.code == "over_cap"
    small = snap([grp("g1", 1_000, active=False)])
    assert isinstance(check_action(Activate("c1", "campaign"), small), NeedsApproval)


def test_campaign_without_own_groups_in_snapshot_cannot_be_activated():
    s = snap([grp("g1", 1_000, active=False)],
             campaigns=(CampaignState(id="c1", spent=usd(0)), CampaignState(id="c2", spent=usd(0))),
             own_object_ids=frozenset({"c1", "c2", "g1"}))
    verdict = check_action(Activate("c2", "campaign"), s)
    assert isinstance(verdict, Denied) and verdict.code == "unknown_object"


def test_campaign_level_budget_is_denied():
    s = snap([grp("g1")])
    verdict = check_action(CreateObject("campaign", budget=usd(3_000)), s)
    assert isinstance(verdict, Denied) and verdict.code == "campaign_budget"


# --- 5. запрет по умолчанию -------------------------------------------------------------------

@dataclass(frozen=True)
class SetCampaignBudget:
    campaign_id: str
    budget: Money


def test_unknown_action_types_are_denied_even_when_halted():
    for snapshot in (snap([grp("g1")]), snap([grp("g1")], halted=True, mode="autonomous")):
        for action in (SetCampaignBudget("c1", usd(10**9)), OptimizerDecision("scale", "g1", usd(10**9), "")):
            verdict = check_action(action, snapshot)
            assert isinstance(verdict, Denied) and verdict.code == "unknown_action"


# --- 7. защитный лимит ------------------------------------------------------------------------

def test_spend_cap_raise_within_reserve_is_free_in_autonomous_and_needs_button_in_confirm():
    s_auto = snap([grp("g1")], mode="autonomous")
    s_confirm = snap([grp("g1")])
    raise_cap = SetSpendCap("c1", usd(60_000))      # резерв: 100 + 600 = 700 $
    assert check_action(raise_cap, s_auto) == Allowed()
    assert isinstance(check_action(raise_cap, s_confirm), NeedsApproval)
    over = check_action(SetSpendCap("c1", usd(70_001)), s_auto)
    assert isinstance(over, Denied) and over.code == "spend_cap"


# --- 10. целостность снимка -------------------------------------------------------------------

def test_snapshot_rejects_nonsense_inputs():
    good = [grp("g1")]
    with pytest.raises(ValueError):
        snap(good, overspend=Decimal(0))
    with pytest.raises(ValueError):
        snap(good, overspend=Decimal("0.5"))
    with pytest.raises(ValueError):
        snap(good, overspend=Decimal("-1"))
    with pytest.raises(ValueError):
        snap(good, spent_month=usd(-1))
    with pytest.raises(ValueError):
        snap([grp("g1"), grp("g1", active=False)])
    with pytest.raises(ValueError):
        snap([grp("g1", ads=(AdState(id="x"),)), grp("g2", ads=(AdState(id="x"),))])
    with pytest.raises(CurrencyMismatch):
        snap(good, campaigns=(CampaignState(id="c1", spent=usd(0), spend_cap=Money(5, "JPY")),))
    with pytest.raises(CurrencyMismatch):
        snap([replace(grp("g1"), spent_today=Money(5, "KRW"))])
    with pytest.raises(CurrencyMismatch):
        snap([replace(grp("g1"), spend=Money(5, "KRW"))])
    with pytest.raises(ValueError):
        snap([replace(grp("g1"), spend=usd(-5))])


# --- 9. валюта контекста плана ----------------------------------------------------------------

def make_ctx(**over) -> PlanContext:
    coffee = (InterestInfo("6003", "Кофе", 3_000_000),)
    base = dict(
        currency="USD", daily_cap=usd(5_000), min_budget_imp=usd(200), min_budget_high_freq=usd(500),
        interest_catalog={"кофе": coffee}, engagement_audience_size=5_000, lookalike_source_size=5_000,
        broad_audience_size=900_000, facts=("Работаем ежедневно",),
        creative_ids=frozenset({"m1", "m2", "m3"}), language="ru", default_countries=("US",),
        demo=True,
    )
    return PlanContext(**{**base, **over})


def test_plan_context_currency_must_match_cap_and_minimums():
    proposal = PlanProposalInput("direct_messages", ())
    with pytest.raises(CurrencyMismatch):
        validate_plan(proposal, make_ctx(daily_cap=Money(5_000, "EUR")))
    with pytest.raises(CurrencyMismatch):
        validate_plan(proposal, make_ctx(min_budget_high_freq=Money(500, "EUR")))


# --- 12. настоящие схемы Стратега и Копирайтера -------------------------------------------------

def variants(*specs) -> AdCopySet:
    return AdCopySet(variants=tuple(
        AdVariant(kind=kind, creative_id=cid, primary_text=text, headline="Кофе")
        for kind, cid, text in specs))


COPY_OK = variants(("new_creative", "m1", "Свежий кофе каждое утро"), ("existing_post", "m2", ""))


def strategist_plan() -> MediaPlanProposal:
    return MediaPlanProposal(
        objective=Objective.DIRECT_MESSAGES, objective_rationale="люди пишут в Direct", notes="заметка",
        groups=(
            GroupProposal(name="Широкая", hypothesis=Hypothesis.BROAD, budget_share=Decimal("0.6"),
                          locations=("US",), rationale="охват"),
            GroupProposal(name="Кофеманы", hypothesis=Hypothesis.INTERESTS, budget_share=Decimal("0.4"),
                          interest_keywords=("кофе",), locations=("US",), age_min=20, age_max=45),
        ))


def test_real_strategist_and_copywriter_objects_pass_without_blockers_or_renames():
    plan = validate_plan(strategist_plan(), make_ctx(), copy=[COPY_OK, COPY_OK])
    assert plan.ok and plan.blockers_ru == () and len(plan.groups) == 2
    assert plan.notes_ru == ()          # name, rationale, locations — законные поля
    assert plan.groups[0].countries == ("US",) and plan.groups[1].interests[0].id == "6003"
    assert plan.groups[1].ads[1].kind == "existing_post"


def test_legit_strategist_fields_in_a_dict_do_not_produce_discard_notes():
    raw = strategist_plan().model_dump(mode="json")
    raw["groups"] = [dict(g, ads=[{"kind": "new_creative", "primary_text": "Свежий кофе", "creative_id": "m1",
                                   "headline": "Кофе", "cta": "LEARN_MORE"},
                                  {"kind": "existing_post", "creative_id": "m2"}]) for g in raw["groups"]]
    plan = validate_plan(raw, make_ctx())
    assert plan.ok and plan.notes_ru == ()


def test_schema_plan_without_copy_has_no_ads_and_is_blocked_with_reasons():
    plan = validate_plan(strategist_plan(), make_ctx())
    assert not plan.ok and any("нужно не меньше 2" in n for n in plan.notes_ru)


# --- 13. вердикты Контролёра ----------------------------------------------------------------

def verdict_ok(index: int) -> AdVerdict:
    return AdVerdict(ad_index=index, checks=tuple(ChecklistItem(rule=r, ok=True) for r in range(1, 10)))


def verdict_bad(index: int) -> AdVerdict:
    return AdVerdict(ad_index=index, violations=(Violation(rule=6, description="выдуманная скидка", fix="убрать"),))


def verdict_partial(index: int) -> AdVerdict:
    return AdVerdict(ad_index=index, checks=(ChecklistItem(rule=1, ok=True),))


THREE = variants(("new_creative", "m1", "Свежий кофе"), ("new_creative", "m2", "Кофе рядом"),
                 ("existing_post", "m3", ""))


def group_plan(verdict: ComplianceVerdict, ctx=None, copy=THREE):
    ctx = ctx or make_ctx(demo=False)
    proposal = MediaPlanProposal(objective=Objective.REACH, groups=(
        GroupProposal(name="A", hypothesis=Hypothesis.BROAD, budget_share=Decimal("0.5"), locations=("US",)),
        GroupProposal(name="B", hypothesis=Hypothesis.BROAD, budget_share=Decimal("0.5"), locations=("US",)),
    ))
    return validate_plan(proposal, ctx, copy=[copy, copy], verdicts=[verdict, verdict])


def test_only_ok_verdicts_pass_outside_demo():
    verdict = ComplianceVerdict(ads=(verdict_ok(0), verdict_ok(1), verdict_bad(2)))
    plan = group_plan(verdict)
    assert [len(g.ads) for g in plan.groups] == [2, 2]
    assert any("Контролёр отклонил" in n and "выдуманная скидка" in n for n in plan.notes_ru)


def test_needs_review_is_dropped_outside_demo_and_kept_in_demo():
    verdict = ComplianceVerdict(ads=(verdict_ok(0), verdict_ok(1), verdict_partial(2)))
    assert [len(g.ads) for g in group_plan(verdict).groups] == [2, 2]
    assert any("needs_review" in n for n in group_plan(verdict).notes_ru)
    assert [len(g.ads) for g in group_plan(verdict, make_ctx(demo=True)).groups] == [3, 3]


def test_violation_is_dropped_even_in_demo_and_missing_verdict_counts_as_review():
    verdict = ComplianceVerdict(ads=(verdict_ok(0), verdict_bad(1), verdict_ok(2)))
    assert [len(g.ads) for g in group_plan(verdict, make_ctx(demo=True)).groups] == [2, 2]
    partial = ComplianceVerdict(ads=(verdict_ok(0), verdict_ok(1)))
    assert [len(g.ads) for g in group_plan(partial).groups] == [2, 2]


def test_adult_only_and_category_from_verdict_restrict_the_plan():
    verdict = ComplianceVerdict(ads=(verdict_ok(0), verdict_ok(1), verdict_ok(2)), adult_only=True,
                                category=SpecialCategory.HOUSING)
    plan = group_plan(verdict)
    assert all(g.adult_only and g.age_min == 18 for g in plan.groups)
    assert plan.special_category is SpecialCategory.HOUSING
    assert all((g.age_min, g.age_max) == (18, 65) for g in plan.groups)


# --- 4. R23 ---------------------------------------------------------------------------------

def ad(kind, cid, text="Свежий кофе"):
    return AdInput(kind, text, cid)


def test_group_with_fewer_than_two_valid_ads_is_dropped():
    proposal = PlanProposalInput("direct_messages", (
        GroupInput("broad", Decimal("0.5"), ads=(ad("new_creative", "m1"), ad("new_creative", "FOREIGN"))),
        GroupInput("broad", Decimal("0.5"), ads=(ad("new_creative", "m1"), ad("new_creative", "m2")))))
    plan = validate_plan(proposal, make_ctx())
    assert len(plan.groups) == 1 and any("нужно не меньше 2" in n for n in plan.notes_ru)


def test_uploaded_media_require_at_least_one_new_creative_per_group():
    only_posts = GroupInput("broad", Decimal("0.5"), ads=(ad("existing_post", "m1"), ad("existing_post", "m2")))
    mixed = GroupInput("broad", Decimal("0.5"), ads=(ad("existing_post", "m1"), ad("new_creative", "m2")))
    ctx = make_ctx(has_uploaded_media=True)
    plan = validate_plan(PlanProposalInput("direct_messages", (only_posts, mixed)), ctx)
    assert len(plan.groups) == 1 and plan.groups[0].ads[1].kind == "new_creative"
    assert any("присланные медиа" in n for n in plan.notes_ru)
    no_media = validate_plan(PlanProposalInput("direct_messages", (only_posts, mixed)), make_ctx())
    assert len(no_media.groups) == 2
