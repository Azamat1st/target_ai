"""Второй круг атак: обход исправлений (кампания на паузе, подмена ключа, порядок пакета)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from target_ai.agents.schemas import (
    AdCopySet, AdVariant, AdVerdict, ChecklistItem, ComplianceVerdict, GroupProposal, Hypothesis,
    MediaPlanProposal, Objective,
)
from target_ai.money import Money
from target_ai.policy import (
    Activate, Allowed, CampaignState, CompanySnapshot, Denied, GroupState, InterestInfo, Launch,
    NeedsApproval, NewGroup, OptimizerDecision, PlanContext, SetDailyBudget, SetSpendCap, Targeting,
    action_key, check_action, check_decisions, validate_plan,
)

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
IG = Targeting.instagram_only(("stream", "story"))


def usd(minor: int) -> Money:
    return Money(minor, "USD")


def grp(gid, budget=1_000, *, active=True, cid="c1"):
    return GroupState(id=gid, name=gid, campaign_id=cid, daily_budget=usd(budget), active=active,
                      active_since=NOW - timedelta(hours=96),
                      last_budget_change_at=NOW - timedelta(hours=72))


def snap(groups, campaigns=None, **over) -> CompanySnapshot:
    groups = tuple(groups)
    campaigns = campaigns if campaigns is not None else (
        CampaignState(id="c1", spent=usd(10_000), spend_cap=usd(50_000)),)
    base = dict(
        company_id=1, currency="USD", timezone="UTC", now=NOW, mode="confirm", level="live",
        is_demo=False, halted=False, monthly_limit=usd(100_000), spent_month=usd(40_000),
        min_budget_imp=usd(200), min_budget_high_freq=usd(500), min_spend_cap=usd(10_000),
        groups=groups, campaigns=tuple(campaigns),
        own_object_ids=frozenset({c.id for c in campaigns} | {g.id for g in groups}),
    )
    return CompanySnapshot(**{**base, **over})


# --- 1. кампания на паузе не тратит ---------------------------------------------------------

def test_paused_campaign_with_active_groups_spends_nothing_and_activation_is_checked():
    paused = (CampaignState(id="c1", spent=usd(0), active=False),)
    groups = [grp("g1", 1_500), grp("g2", 1_500)]
    confirm = snap(groups, paused, spent_month=usd(52_000))           # потолок 30 $
    assert confirm.active_total == 0
    assert isinstance(check_action(Activate("c1", "campaign"), confirm), NeedsApproval)
    tight = snap(groups, paused, mode="autonomous", spent_month=usd(70_000))   # потолок 18,75 $
    verdict = check_action(Activate("c1", "campaign"), tight)
    assert isinstance(verdict, Denied) and verdict.code == "over_cap"


def test_pausing_a_group_of_a_paused_campaign_does_not_fund_a_raise_elsewhere():
    campaigns = (CampaignState(id="c1", spent=usd(0)),
                 CampaignState(id="c2", spent=usd(0), active=False))
    s = snap([grp("A"), grp("Z", cid="c2")], campaigns)
    batch = [OptimizerDecision("pause_group", "Z", None, ""), OptimizerDecision("set_budget", "A", usd(1_200), "")]
    result = {d.target_id: v for d, v in check_decisions(batch, s)}
    assert result["Z"] == Allowed() and isinstance(result["A"], NeedsApproval)


# --- 2. ключ подтверждения привязан к состоянию -----------------------------------------------

def test_key_issued_for_one_state_does_not_pass_another_amount_or_set_of_groups():
    s1 = snap([grp("g1"), grp("g3", 1_000, active=False)])
    act = Activate("g3", "group")
    first = check_action(act, s1)
    assert isinstance(first, NeedsApproval) and first.key == action_key(act, s1)
    assert check_action(act, s1, approved_keys=frozenset({first.key})) == Allowed()
    s2 = snap([grp("g1"), grp("g3", 1_200, active=False)])             # бюджет «10 → 12 $»
    again = check_action(act, s2, approved_keys=frozenset({first.key}))
    assert isinstance(again, NeedsApproval) and again.key != first.key


def test_campaign_approval_dies_when_a_new_paused_group_joins():
    one = snap([grp("p1", 1_000, active=False)])
    two = snap([grp("p1", 1_000, active=False), grp("p2", 2_000, active=False)])
    act = Activate("c1", "campaign")
    key = frozenset({action_key(act, one)})
    assert check_action(act, one, approved_keys=key) == Allowed()
    assert isinstance(check_action(act, two, approved_keys=key), NeedsApproval)


def test_identical_launch_cannot_reuse_a_consumed_key():
    launch = Launch((NewGroup(usd(1_000), "high_freq", IG),))
    key = frozenset({action_key(launch, snap([]))})
    assert check_action(launch, snap([]), approved_keys=key) == Allowed()
    assert isinstance(check_action(launch, snap([grp("n1")]), approved_keys=key), NeedsApproval)


def test_key_changes_with_spend_cap_and_monthly_limit():
    act = SetSpendCap("c1", usd(60_000))
    base = snap([grp("g1")])
    other_cap = snap([grp("g1")], (CampaignState(id="c1", spent=usd(10_000), spend_cap=usd(40_000)),))
    assert action_key(act, base) != action_key(act, other_cap)
    assert action_key(act, base) != action_key(act, replace(base, monthly_limit=usd(90_000)))


# --- 3. вердикт Контролёра обязателен вне демо -------------------------------------------------

def ctx(**over) -> PlanContext:
    base = dict(
        currency="USD", daily_cap=usd(5_000), min_budget_imp=usd(200), min_budget_high_freq=usd(500),
        interest_catalog={"кофе": (InterestInfo("6003", "Кофе", 3_000_000),)},
        facts=(), creative_ids=frozenset({"m1", "m2"}), language="ru", default_countries=("US",))
    return PlanContext(**{**base, **over})


def plan_and_copy():
    plan = MediaPlanProposal(objective=Objective.REACH, groups=tuple(
        GroupProposal(name=n, hypothesis=Hypothesis.BROAD, budget_share=Decimal("0.5"), locations=("US",))
        for n in ("A", "B")))
    copy = AdCopySet(variants=(AdVariant(kind="new_creative", creative_id="m1", primary_text="Кофе", headline="Кофе"),
                               AdVariant(kind="new_creative", creative_id="m2", primary_text="Чай", headline="Чай")))
    return plan, [copy, copy]


def all_ok() -> ComplianceVerdict:
    return ComplianceVerdict(ads=tuple(
        AdVerdict(ad_index=i, checks=tuple(ChecklistItem(rule=r, ok=True) for r in range(1, 10))) for i in (0, 1)))


def test_missing_controller_verdict_drops_ads_outside_demo_and_passes_in_demo():
    plan, copy = plan_and_copy()
    outside = validate_plan(plan, ctx(demo=False), copy=copy, verdicts=None)
    assert not outside.ok and any("нет вердикта" in n for n in outside.notes_ru)
    assert validate_plan(plan, ctx(demo=True), copy=copy, verdicts=None).ok


def test_group_without_its_own_verdict_is_dropped_outside_demo():
    plan, copy = plan_and_copy()
    result = validate_plan(plan, ctx(demo=False), copy=copy, verdicts=[all_ok()])
    assert [len(g.ads) for g in result.groups] == [2]
    assert validate_plan(plan, ctx(demo=False), copy=copy, verdicts=[all_ok(), all_ok()]).ok


# --- 4. привязка кампаний к группам -----------------------------------------------------------

# --- 5. база пакета не приходит от вызывающего ----------------------------------------------

def test_check_action_has_no_spend_baseline_parameter():
    with pytest.raises(TypeError):
        check_action(SetDailyBudget("g1", usd(1_200)), snap([grp("g1")]), spend_baseline=10**9)


# --- 6. сумма защитных лимитов ----------------------------------------------------------------

def test_sum_of_spend_caps_over_reserve_needs_button_in_both_modes():
    campaigns = (CampaignState(id="c1", spent=usd(10_000), spend_cap=usd(50_000)),
                 CampaignState(id="c2", spent=usd(0)))
    for mode in ("confirm", "autonomous"):
        s = snap([grp("g1")], campaigns, mode=mode)                          # резерв: 100 + 600 = 700 $
        over = check_action(SetSpendCap("c2", usd(30_000)), s)             # 500 + 300 = 800 $
        assert isinstance(over, NeedsApproval) and over.kind == "spend_cap_increase"
    fits = check_action(SetSpendCap("c2", usd(20_000)), snap([grp("g1")], campaigns, mode="autonomous"))
    assert fits == Allowed()


# --- 8. порядок исполнения пакета -------------------------------------------------------------

def test_check_decisions_returns_verdicts_in_execution_order():
    s = snap([grp("A"), grp("B"), grp("C")])
    shuffled = [OptimizerDecision("scale", "C", usd(1_200), ""), OptimizerDecision("set_budget", "A", usd(1_200), ""),
                OptimizerDecision("set_budget", "B", usd(800), ""), OptimizerDecision("pause_group", "C", None, "")]
    order = [(d.kind, d.target_id) for d, _ in check_decisions(shuffled, s)]
    assert order == [("pause_group", "C"), ("set_budget", "B"), ("set_budget", "A"), ("scale", "C")]


def test_warning_with_budget_is_never_an_action():
    assert OptimizerDecision("fatigue_warning", "g1", usd(10**9), "").to_action() is None


# --- 9. состав кампании выводится из групп снимка (a11) ---------------------------------------

LAST_DAY = datetime(2026, 11, 30, 12, 0, tzinfo=UTC)


def _paused_campaign_with_unlisted_group(**over):
    """c1 на паузе; g1 14 $ и g2 30 $ (активна) — обе принадлежат c1; остаток месяца 25 $, последний день."""
    campaigns = (CampaignState(id="c1", spent=usd(0), active=False),)
    return snap([grp("g1", 1_400), grp("g2", 3_000)], campaigns, **{
        "now": LAST_DAY, "spent_month": usd(97_500), **over})


def test_campaign_activation_counts_every_group_of_the_campaign_from_the_snapshot():
    s = _paused_campaign_with_unlisted_group(mode="autonomous")
    verdict = check_action(Activate("c1", "campaign"), s)                 # реально включится 44 $ при потолке < 44 $
    assert isinstance(verdict, Denied) and verdict.code == "over_cap"
    confirm = _paused_campaign_with_unlisted_group(spent_month=usd(80_000))       # потолок выше 44 $
    button = check_action(Activate("c1", "campaign"), confirm)
    assert isinstance(button, NeedsApproval) and "до 44,00" in button.summary_ru


def test_key_for_a_campaign_does_not_survive_a_change_in_its_groups():
    act = Activate("c1", "campaign")
    before = _paused_campaign_with_unlisted_group(spent_month=usd(80_000))
    key = check_action(act, before).key
    raised = snap([grp("g1", 1_400), grp("g2", 5_000)], before.campaigns, now=LAST_DAY, spent_month=usd(80_000))
    again = check_action(act, raised, approved_keys=frozenset({key}))
    assert isinstance(again, NeedsApproval) and again.key != key


def test_group_of_an_unknown_campaign_makes_the_snapshot_invalid():
    with pytest.raises(ValueError):
        snap([grp("x", cid="ghost")], (CampaignState(id="c1", spent=usd(0)),))


def test_batch_key_shown_to_the_user_is_the_key_checked_against_approvals():
    s = snap([grp("A"), grp("B")])
    batch = [OptimizerDecision("pause_group", "B", None, ""), OptimizerDecision("scale", "A", usd(1_200), "")]
    first = {d.kind: v for d, v in check_decisions(batch, s)}
    assert isinstance(first["scale"], NeedsApproval)
    again = {d.kind: v for d, v in check_decisions(batch, s, approved_keys=frozenset({first["scale"].key}))}
    assert again["scale"] == Allowed()


def test_launch_key_differs_between_launches_with_different_composition():
    s = snap([grp("g1")])
    groups = (NewGroup(usd(1_000), "high_freq", IG, id="n1"),)
    one = action_key(Launch(groups, tag="t1"), s)
    assert one != action_key(Launch(groups, tag="t2"), s)
    assert one != action_key(Launch((NewGroup(usd(1_000), "high_freq", IG, id="n2"),), tag="t1"), s)


def test_campaign_with_blank_id_makes_the_snapshot_invalid():
    for blank in ("", "   "):
        with pytest.raises(ValueError):
            snap([grp("g1", cid="")], (CampaignState(id=blank, spent=usd(0)),))
