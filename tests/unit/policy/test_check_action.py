from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from target_ai.money import CurrencyMismatch, Money
from target_ai.policy import (
    Activate, AdState, Allowed, CampaignState, CompanySnapshot, CreateObject, Delete, Denied, GroupState,
    Launch, NeedsApproval, NewGroup, Pause, SetDailyBudget, SetMonthlyLimit, SetSpendCap,
    Targeting, action_key, check_action, increases_spend,
)

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
IG = Targeting.instagram_only(("stream", "story"))


def usd(minor: int) -> Money:
    return Money(minor, "USD")


def make_snapshot(**over) -> CompanySnapshot:
    """Лимит 1000 $, потрачено 400 $, 16 дней → daily_cap 37,50 $; активно 30 $ из двух групп."""
    base = dict(
        company_id=1, currency="USD", timezone="UTC", now=NOW, mode="confirm", level="live",
        is_demo=False, halted=False, monthly_limit=usd(100_000), spent_month=usd(40_000),
        min_budget_imp=usd(200), min_budget_high_freq=usd(500), min_spend_cap=usd(10_000),
        groups=(
            GroupState(id="g1", campaign_id="c1", daily_budget=usd(1_500), spent_today=usd(500),
                       last_budget_change_at=NOW - timedelta(days=3), ads=(AdState(id="a1"),)),
            GroupState(id="g2", campaign_id="c1", daily_budget=usd(1_500),
                       last_budget_change_at=NOW - timedelta(days=3)),
            GroupState(id="g3", campaign_id="c1", daily_budget=usd(1_000), active=False),
        ),
        campaigns=(CampaignState(id="c1", spent=usd(10_000), spend_cap=usd(50_000)),),
        own_object_ids=frozenset({"c1", "g1", "g2", "g3", "a1"}),
    )
    return CompanySnapshot(**{**base, **over})


def keys(snapshot, *actions) -> frozenset[str]:
    return frozenset(action_key(a, snapshot) for a in actions)


def code(decision) -> str:
    assert isinstance(decision, Denied), decision
    return decision.code


def test_snapshot_cap_matches_hand_calculation():
    assert make_snapshot().daily_cap == usd(3_750)


def test_snapshot_rejects_mixed_currencies():
    with pytest.raises(CurrencyMismatch):
        make_snapshot(spent_month=Money(1, "EUR"))


# --- порядок проверок ---------------------------------------------------------------------

def test_halted_blocks_enabling_and_increasing():
    snap = make_snapshot(halted=True, mode="autonomous")
    assert code(check_action(Activate("g3", "group"), snap)) == "halted"
    assert code(check_action(SetDailyBudget("g1", usd(1_700)), snap)) == "halted"
    assert code(check_action(Launch((NewGroup(usd(500), "high_freq", IG),)), snap)) == "halted"


def test_halted_still_allows_pausing_and_decreasing():
    snap = make_snapshot(halted=True)
    assert check_action(Pause("g1"), snap) == Allowed()
    assert check_action(SetDailyBudget("g1", usd(1_300)), snap) == Allowed()


def test_halted_is_checked_before_ownership():
    snap = make_snapshot(halted=True)
    assert code(check_action(Activate("foreign", "group"), snap)) == "halted"


def test_foreign_object_is_denied_for_every_object_action():
    snap = make_snapshot()
    for action in (Pause("x"), Activate("x", "ad"), SetDailyBudget("x", usd(900)),
                   SetSpendCap("x", usd(20_000)), Delete("x"), CreateObject("ad", parent_id="x")):
        assert code(check_action(action, snap)) == "not_own", action


def test_ownership_is_checked_before_level():
    snap = make_snapshot(level="demo", is_demo=False)
    assert code(check_action(Pause("x"), snap)) == "not_own"


@pytest.mark.parametrize(("level", "is_demo"), [("demo", False), ("live", True), ("test", True)])
def test_level_and_company_kind_must_match(level, is_demo):
    snap = make_snapshot(level=level, is_demo=is_demo)
    assert code(check_action(Pause("g1"), snap)) == "level_mismatch"


def test_demo_company_on_demo_level_passes():
    assert check_action(Pause("g1"), make_snapshot(level="demo", is_demo=True)) == Allowed()


@pytest.mark.parametrize(
    "targeting",
    [
        None,
        Targeting(),
        Targeting(("instagram", "facebook"), ("stream",), ("mobile",)),
        Targeting(("facebook",), ("stream",), ("mobile",)),
        Targeting(("instagram",), (), ("mobile",)),
        Targeting(("instagram",), ("feed_unknown",), ("mobile",)),
        Targeting(("instagram",), ("stream",), ("desktop",)),
        Targeting(("instagram",), ("stream",), ("mobile",), placement_soft_opt_out=True),
    ],
)
def test_non_instagram_or_incomplete_targeting_is_denied(targeting):
    snap = make_snapshot()
    launch = Launch((NewGroup(usd(500), "high_freq", targeting),))
    assert code(check_action(launch, snap)) == "placements"
    create = CreateObject("group", parent_id="c1", budget=usd(500), goal_kind="high_freq",
                          targeting=targeting)
    assert code(check_action(create, snap)) == "placements"


def test_placements_are_checked_before_amounts():
    bad = Targeting(("facebook",), ("stream",), ("mobile",))
    assert code(check_action(Launch((NewGroup(usd(0), "high_freq", bad),)), make_snapshot())) == "placements"


# --- суммы --------------------------------------------------------------------------------

def test_foreign_currency_is_denied_without_comparison_error():
    assert code(check_action(SetDailyBudget("g1", Money(1_600, "EUR")), make_snapshot())) == "currency"
    assert code(check_action(SetMonthlyLimit(Money(1, "JPY")), make_snapshot())) == "currency"


@pytest.mark.parametrize("minor", [0, -500, -10**9])
def test_non_positive_amounts_are_denied(minor):
    assert code(check_action(SetDailyBudget("g1", usd(minor)), make_snapshot())) == "non_positive"
    launch = Launch((NewGroup(usd(minor), "high_freq", IG),))
    assert code(check_action(launch, make_snapshot())) == "non_positive"


def test_launch_above_cap_is_denied_but_exact_headroom_goes_to_approval():
    snap = make_snapshot()  # потолок 3750, занято 3000, запас 750
    over = Launch((NewGroup(usd(751), "high_freq", IG),))
    exact = Launch((NewGroup(usd(750), "high_freq", IG),))
    assert code(check_action(over, snap)) == "over_cap"
    assert isinstance(check_action(exact, snap), NeedsApproval)


def test_absurd_budget_is_denied_even_in_autonomous_mode():
    snap = make_snapshot(mode="autonomous")
    assert code(check_action(Launch((NewGroup(usd(10**9), "high_freq", IG),)), snap)) == "over_cap"
    assert code(check_action(SetDailyBudget("g1", usd(10**9)), snap)) == "over_cap"


def test_resuming_group_that_breaks_cap_is_denied():
    assert code(check_action(Activate("g3", "group"), make_snapshot())) == "over_cap"


def test_group_below_meta_minimum_is_denied():
    snap = make_snapshot()
    create = CreateObject("group", parent_id="c1", budget=usd(499), goal_kind="high_freq", targeting=IG)
    assert code(check_action(create, snap)) == "below_min"
    ok = CreateObject("group", parent_id="c1", budget=usd(500), goal_kind="high_freq", targeting=IG)
    assert check_action(ok, snap) == Allowed()
    reach = CreateObject("group", parent_id="c1", budget=usd(200), goal_kind="imp", targeting=IG)
    assert check_action(reach, snap) == Allowed()


def test_lowering_budget_under_meta_minimum_is_denied_even_for_limit():
    snap = make_snapshot(groups=(
        GroupState(id="g1", campaign_id="c1", daily_budget=usd(600), last_budget_change_at=NOW - timedelta(days=2)),
    ))
    assert code(check_action(SetDailyBudget("g1", usd(480), lowers_for_limit=True), snap)) == "below_min"


def test_lowering_budget_under_spent_today_plus_ten_percent_is_denied():
    snap = make_snapshot(groups=(
        GroupState(id="g1", campaign_id="c1", daily_budget=usd(1_500), spent_today=usd(1_300),
                   last_budget_change_at=NOW - timedelta(days=2)),
    ))
    assert code(check_action(SetDailyBudget("g1", usd(1_429)), snap)) == "below_spent"
    assert check_action(SetDailyBudget("g1", usd(1_430)), snap) == Allowed()


def test_reduction_is_allowed_even_when_company_is_already_over_cap():
    snap = make_snapshot(spent_month=usd(90_000))  # потолок 625, активно 3000
    assert check_action(SetDailyBudget("g1", usd(1_200), lowers_for_limit=True), snap) == Allowed()
    assert check_action(Pause("g2"), snap) == Allowed()


def test_spend_cap_must_stay_within_campaign_spent_plus_remaining():
    snap = make_snapshot(mode="autonomous")  # 10 000 + 60 000 = 70 000
    assert code(check_action(SetSpendCap("c1", usd(70_001)), snap)) == "spend_cap"
    assert check_action(SetSpendCap("c1", usd(70_000)), snap) == Allowed()


def test_spend_cap_below_meta_minimum_is_denied():
    assert code(check_action(SetSpendCap("c1", usd(9_999)), make_snapshot())) == "spend_cap"


def test_spend_cap_floor_above_remaining_is_allowed_up_to_meta_minimum():
    snap = make_snapshot(spent_month=usd(97_000), mode="autonomous")  # остаток 30 $, минимум Meta 100 $
    campaign = CampaignState(id="c1", spent=usd(0), spend_cap=None)
    snap = replace(snap, campaigns=(campaign,))
    # поднятый до минимума Meta лимит выше остатка — кнопка нужна даже в «Автономном»
    floor = SetSpendCap("c1", usd(10_000))
    decision = check_action(floor, snap)
    assert isinstance(decision, NeedsApproval) and decision.kind == "spend_cap_increase"
    assert check_action(floor, snap, approved_keys=keys(snap, floor)) == Allowed()
    assert code(check_action(SetSpendCap("c1", usd(10_001)), snap)) == "spend_cap"


# --- изменение бюджета: 20% и 24 часа -----------------------------------------------------

def test_change_above_twenty_percent_is_denied():
    assert code(check_action(SetDailyBudget("g1", usd(1_801)), make_snapshot())) == "change_too_big"
    assert isinstance(check_action(SetDailyBudget("g1", usd(1_800)), make_snapshot()), NeedsApproval)


def test_big_cut_without_limit_reason_is_denied_but_limit_cut_is_allowed():
    snap = make_snapshot()
    assert code(check_action(SetDailyBudget("g1", usd(1_000)), snap)) == "change_too_big"
    assert check_action(SetDailyBudget("g1", usd(1_000), lowers_for_limit=True), snap) == Allowed()


def test_second_change_within_24_hours_is_denied():
    recent = GroupState(id="g1", campaign_id="c1", daily_budget=usd(1_500),
                        last_budget_change_at=NOW - timedelta(hours=23, minutes=59))
    snap = make_snapshot(groups=(recent,))
    assert code(check_action(SetDailyBudget("g1", usd(1_400)), snap)) == "too_soon"
    assert check_action(SetDailyBudget("g1", usd(1_400), lowers_for_limit=True), snap) == Allowed()


def test_change_exactly_24_hours_after_previous_is_allowed():
    exact = GroupState(id="g1", campaign_id="c1", daily_budget=usd(1_500),
                       last_budget_change_at=NOW - timedelta(hours=24))
    assert check_action(SetDailyBudget("g1", usd(1_400)), make_snapshot(groups=(exact,))) == Allowed()


def test_budget_change_for_group_without_state_is_denied():
    snap = make_snapshot(own_object_ids=frozenset({"c1", "g1", "g2", "g3", "ghost"}))
    assert code(check_action(SetDailyBudget("ghost", usd(500)), snap)) == "unknown_group"


# --- подтверждение ------------------------------------------------------------------------

def test_confirm_mode_asks_for_every_spend_increase():
    snap = make_snapshot()
    scale = check_action(SetDailyBudget("g1", usd(1_700)), snap)
    assert isinstance(scale, NeedsApproval) and scale.kind == "budget_increase"
    launch = check_action(Launch((NewGroup(usd(500), "high_freq", IG),)), snap)
    assert isinstance(launch, NeedsApproval) and launch.kind == "launch"
    paused = make_snapshot(groups=(replace(snap.groups[0], daily_budget=usd(500)), snap.groups[2]))
    resume = check_action(Activate("g3", "group"), paused)
    assert isinstance(resume, NeedsApproval) and resume.kind == "resume"
    assert "$" in resume.summary_ru


def test_consumed_approval_turns_needs_approval_into_allowed():
    snap = make_snapshot()
    assert check_action(SetDailyBudget("g1", usd(1_700)), snap, approved_keys=keys(snap, SetDailyBudget("g1", usd(1_700)))) == Allowed()


def test_approval_never_overrides_a_denial():
    snap = make_snapshot()
    assert code(check_action(SetDailyBudget("g1", usd(10**9)), snap, approved_keys=keys(snap, SetDailyBudget("g1", usd(10**9))))) == "over_cap"


def test_autonomous_mode_increases_alone_within_cap():
    snap = make_snapshot(mode="autonomous")
    assert check_action(SetDailyBudget("g1", usd(1_700)), snap) == Allowed()


def test_autonomous_launch_with_spend_cap_floor_still_needs_button():
    snap = make_snapshot(spent_month=usd(97_000), mode="autonomous", min_budget_high_freq=usd(100),
                         campaigns=(), groups=())
    launch = Launch((NewGroup(usd(150), "high_freq", IG),), spend_cap=usd(10_000))
    decision = check_action(launch, replace(snap, groups=()))
    assert isinstance(decision, NeedsApproval) and decision.kind == "launch"
    assert check_action(launch, replace(snap, groups=()), approved_keys=keys(replace(snap, groups=()), launch)) == Allowed()


def test_monthly_limit_raise_needs_button_in_any_mode_and_cut_does_not():
    for mode in ("confirm", "autonomous"):
        snap = make_snapshot(mode=mode)
        up = check_action(SetMonthlyLimit(usd(150_000)), snap)
        assert isinstance(up, NeedsApproval) and up.kind == "monthly_limit_increase"
        assert check_action(SetMonthlyLimit(usd(80_000)), snap) == Allowed()
    assert code(check_action(SetMonthlyLimit(usd(0)), make_snapshot())) == "non_positive"


def test_spend_cap_raise_needs_approval_in_confirm_mode():
    decision = check_action(SetSpendCap("c1", usd(60_000)), make_snapshot())
    assert isinstance(decision, NeedsApproval) and decision.kind == "spend_cap_increase"
    assert check_action(SetSpendCap("c1", usd(40_000)), make_snapshot()) == Allowed()


def test_pause_delete_and_create_never_need_approval():
    snap = make_snapshot()
    assert check_action(Pause("g1"), snap) == Allowed()
    assert check_action(Delete("g3"), snap) == Allowed()
    assert check_action(CreateObject("ad", parent_id="g1"), snap) == Allowed()


def test_reasons_are_russian_and_show_amounts():
    decision = check_action(Launch((NewGroup(usd(751), "high_freq", IG),)), make_snapshot())
    assert "7,51 $" in decision.reason_ru and "37,50 $" in decision.reason_ru


# --- что считается увеличением расхода ----------------------------------------------------

@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (Launch((NewGroup(usd(500), "high_freq", IG),)), True),
        (Activate("g3", "group"), True),
        (Activate("c1", "campaign"), True),   # внутри кампании есть выключенная группа
        (Activate("g1", "group"), False),     # уже активна
        (Activate("a1", "ad"), False),
        (SetDailyBudget("g1", usd(1_600)), True),
        (SetDailyBudget("g1", usd(1_400)), False),
        (SetDailyBudget("g3", usd(2_000)), False),  # группа на паузе: активная сумма не растёт
        (SetMonthlyLimit(usd(100_001)), True),
        (SetMonthlyLimit(usd(99_999)), False),
        (SetSpendCap("c1", usd(50_001)), True),
        (SetSpendCap("c1", usd(49_999)), False),
        (Pause("g1"), False),
        (Delete("g1"), False),
        (CreateObject("group", parent_id="c1", budget=usd(500), goal_kind="high_freq", targeting=IG), False),
    ],
)
def test_increase_of_spend_is_defined_by_sum_of_active_daily_budgets(action, expected):
    assert increases_spend(action, make_snapshot()) is expected


def test_unknown_objects_count_as_increase_to_stay_safe():
    assert increases_spend(Activate("nobody", "group"), make_snapshot()) is True
    assert increases_spend(SetSpendCap("nobody", usd(1)), make_snapshot()) is True
