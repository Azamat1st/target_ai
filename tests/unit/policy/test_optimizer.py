from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from target_ai.money import Money
from target_ai.policy import (
    AdState, Allowed, CampaignState, CompanySnapshot, GroupState, NeedsApproval, OptimizerRules, Pause,
    SetDailyBudget, check_action, check_decisions, decide, enforce_pace,
)

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
TARGET = Money(200, "USD")  # целевая цена 2,00 $


def usd(minor: int) -> Money:
    return Money(minor, "USD")


def grp(gid, budget=1_000, *, spend=None, results=0, hours=96, changed_hours=72, name=None,
        active=True, ads=(), frequency="1.0", goal="high_freq"):
    return GroupState(
        id=gid, name=name or gid, campaign_id="c1", daily_budget=usd(budget), active=active,
        goal_kind=goal, spend=usd(spend) if spend is not None else None, results=results,
        active_since=NOW - timedelta(hours=hours),
        last_budget_change_at=NOW - timedelta(hours=changed_hours) if changed_hours else None,
        frequency=Decimal(frequency), ads=tuple(ads),
    )


def snapshot(groups, **over) -> CompanySnapshot:
    """Лимит 1000 $, потрачено 400 $, 16 дней → потолок 37,50 $."""
    base = dict(
        company_id=1, currency="USD", timezone="UTC", now=NOW, mode="confirm", level="live",
        is_demo=False, halted=False, monthly_limit=usd(100_000), spent_month=usd(40_000),
        min_budget_imp=usd(200), min_budget_high_freq=usd(500), groups=tuple(groups),
        campaigns=(CampaignState(id="c1", spent=usd(0)),),
        own_object_ids=frozenset({"c1"} | {g.id for g in groups}
                                 | {a.id for g in groups for a in g.ads}),
    )
    return CompanySnapshot(**{**base, **over})


def run(groups, **over):
    return decide(snapshot(groups, **over), target_cpa=TARGET)


def by_kind(decisions, kind):
    return [d for d in decisions if d.kind == kind]


def test_rules_defaults_match_the_spec_table():
    rules = OptimizerRules()
    assert (rules.min_hours_before_decision, rules.min_spend_target_cpa_multiple) == (72, Decimal("2.0"))
    assert (rules.budget_change_max_pct, rules.budget_change_min_interval_hours) == (20, 24)
    assert (rules.kill_cpa_multiple, rules.strong_cpa_ratio, rules.fatigue_frequency) == (
        Decimal("2.0"), Decimal("1.0"), Decimal("3.0"))
    assert (rules.weak_ad_cpa_multiple, rules.weak_ad_min_impressions) == (Decimal("2.0"), 1_000)


@pytest.mark.parametrize("kwargs", [
    {"budget_change_max_pct": 0}, {"budget_change_max_pct": 101}, {"min_hours_before_decision": -1},
    {"kill_cpa_multiple": Decimal(0)}, {"weak_ad_min_impressions": -5},
])
def test_rules_reject_nonsense_values(kwargs):
    with pytest.raises(ValueError):
        OptimizerRules(**kwargs)


# --- созревание ---------------------------------------------------------------------------

def test_group_younger_than_72_hours_is_left_alone():
    assert run([grp("g1", spend=5_000, results=0, hours=71)]) == []


def test_group_that_spent_less_than_two_target_prices_is_left_alone():
    assert run([grp("g1", spend=399, results=0)]) == []
    assert by_kind(run([grp("g1", spend=400, results=0)]), "pause_group")


def test_exactly_72_hours_is_mature():
    assert by_kind(run([grp("g1", spend=400, results=0, hours=72)]), "pause_group")


# --- выключение группы --------------------------------------------------------------------

def test_group_with_cpa_at_least_double_target_is_paused_with_figures():
    (decision,) = run([grp("g1", spend=1_000, results=2, name="Интересы")])
    assert (decision.kind, decision.target_id, decision.new_budget) == ("pause_group", "g1", None)
    assert "Интересы" in decision.reason_ru
    assert "5,00 $" in decision.reason_ru and "2,00 $" in decision.reason_ru and "4 дн." in decision.reason_ru
    assert decision.to_action() == Pause("g1")


def test_cpa_exactly_double_target_is_paused_and_just_below_is_not():
    assert by_kind(run([grp("g1", spend=800, results=2)]), "pause_group")
    assert not by_kind(run([grp("g1", spend=798, results=2)]), "pause_group")


def test_mature_group_without_results_is_paused():
    (decision,) = run([grp("g1", spend=500, results=0)])
    assert decision.kind == "pause_group" and "ни одного результата" in decision.reason_ru


def test_paused_groups_are_ignored():
    assert run([grp("g1", spend=5_000, results=0, active=False)]) == []


# --- слабые объявления --------------------------------------------------------------------

def test_weak_ad_is_paused_when_group_keeps_another_one():
    ads = [AdState(id="a1", impressions=5_000, spend=usd(300), results=3),
           AdState(id="a2", impressions=1_500, spend=usd(500), results=1)]  # 5,00 $ против 2,00 $ средней
    decisions = run([grp("g1", spend=800, results=4, ads=ads)])
    (decision,) = by_kind(decisions, "pause_ad")
    assert decision.target_id == "a2" and "5,00 $" in decision.reason_ru and "2,00 $" in decision.reason_ru


@pytest.mark.parametrize(
    "ad",
    [
        AdState(id="a2", impressions=999, spend=usd(500), results=1),    # мало показов
        AdState(id="a2", impressions=2_000, spend=usd(150), results=0),  # расход меньше целевой цены
        AdState(id="a2", impressions=2_000, spend=usd(390), results=1),  # цена 3,90 $ < 4,00 $ (2× средней)
    ],
)
def test_ad_is_kept_when_evidence_is_not_enough(ad):
    ads = [AdState(id="a1", impressions=5_000, spend=usd(300), results=3), ad]
    assert not by_kind(run([grp("g1", spend=800, results=4, ads=ads)]), "pause_ad")


def test_ad_without_results_and_spend_above_target_is_paused():
    ads = [AdState(id="a1", impressions=5_000, spend=usd(300), results=3),
           AdState(id="a2", impressions=2_000, spend=usd(250), results=0)]
    assert [d.target_id for d in by_kind(run([grp("g1", spend=800, results=4, ads=ads)]), "pause_ad")] == ["a2"]


def test_at_least_one_ad_always_stays():
    ads = [AdState(id="a1", impressions=3_000, spend=usd(900), results=1),
           AdState(id="a2", impressions=3_000, spend=usd(700), results=1)]  # обе хуже вдвое
    paused = by_kind(run([grp("g1", spend=800, results=4, ads=ads)]), "pause_ad")
    assert [d.target_id for d in paused] == ["a1"]  # слабейшая; вторая остаётся


def test_single_weak_ad_is_never_paused():
    ads = [AdState(id="a1", impressions=3_000, spend=usd(900), results=1)]
    assert not by_kind(run([grp("g1", spend=800, results=4, ads=ads)]), "pause_ad")


def test_ads_of_a_paused_group_get_no_separate_decision():
    ads = [AdState(id="a1", impressions=3_000, spend=usd(900), results=0),
           AdState(id="a2", impressions=3_000, spend=usd(100), results=0)]
    decisions = run([grp("g1", spend=1_000, results=0, ads=ads)])
    assert [d.kind for d in decisions] == ["pause_group"]


# --- перенос бюджета и масштабирование ----------------------------------------------------

def test_budget_moves_from_weak_and_paused_groups_to_strong_without_growing_the_sum():
    groups = [
        grp("weak", spend=700, results=2, name="Интересы"),     # цена 3,50 $ — хуже целевой, лучше 2×
        grp("strong", spend=600, results=5, name="Широкая"),     # цена 1,20 $
        grp("bad", spend=1_000, results=1, name="Похожие"),      # цена 10,00 $ → выключить
    ]
    decisions = run(groups)
    assert {d.target_id: d for d in decisions if d.kind in ("pause_group",)}.keys() == {"bad"}
    budgets = {d.target_id: d.new_budget for d in by_kind(decisions, "set_budget")}
    assert budgets == {"weak": usd(800), "strong": usd(1_200)}
    weak = next(d for d in decisions if d.target_id == "weak")
    assert "−20%" in weak.reason_ru and "3,50 $" in weak.reason_ru and "2,00 $" in weak.reason_ru
    assert by_kind(decisions, "scale") == []  # сильная уже получила своё изменение за сутки
    before = sum(g.daily_budget.amount_minor for g in groups)
    after = before - 1_000 - 200 + 200
    assert after <= before


def test_recipient_gets_no_more_than_twenty_percent_even_if_pool_is_larger():
    groups = [grp("strong", 1_000, spend=600, results=5),
              grp("bad1", 1_000, spend=1_000, results=1), grp("bad2", 1_000, spend=1_000, results=1)]
    budgets = {d.target_id: d.new_budget for d in by_kind(run(groups), "set_budget")}
    assert budgets == {"strong": usd(1_200)}


def test_pool_smaller_than_twenty_percent_limits_the_increase():
    groups = [grp("strong", 3_000, spend=600, results=5), grp("bad", 500, spend=1_000, results=1)]
    budgets = {d.target_id: d.new_budget for d in by_kind(run(groups), "set_budget")}
    assert budgets == {"strong": usd(3_500)}  # +20% было бы 3 600, но освободилось только 5,00 $


def test_weak_group_at_meta_minimum_is_not_cut_and_cut_never_goes_below_it():
    at_min = run([grp("weak", 500, spend=700, results=2), grp("strong", 1_000, spend=600, results=5)])
    assert not [d for d in by_kind(at_min, "set_budget") if d.target_id == "weak"]
    near_min = run([grp("weak", 600, spend=700, results=2), grp("strong", 1_000, spend=600, results=5)])
    assert {d.target_id: d.new_budget for d in by_kind(near_min, "set_budget")}["weak"] == usd(500)


def test_strong_groups_scale_within_headroom_under_daily_cap():
    groups = [grp("s1", spend=600, results=5, changed_hours=0), grp("s2", spend=900, results=6, changed_hours=0)]
    decisions = run(groups)  # потолок 3750, занято 2000, запас 1750
    scales = {d.target_id: d.new_budget for d in by_kind(decisions, "scale")}
    assert scales == {"s1": usd(1_200), "s2": usd(1_200)}
    assert all("+20%" in d.reason_ru for d in by_kind(decisions, "scale"))


def test_scaling_stops_at_daily_cap_even_if_group_could_grow_more():
    groups = [grp("s1", 1_600, spend=600, results=5), grp("s2", 1_600, spend=900, results=6)]
    scales = {d.target_id: d.new_budget for d in by_kind(run(groups), "scale")}
    # потолок 3750, занято 3200, запас 550: лучшей (дешевле результат) — +320, второй — остаток 230
    assert scales == {"s1": usd(1_920), "s2": usd(1_830)}
    assert sum(scales.values(), usd(0)).amount_minor <= 3_750


def test_no_scaling_when_company_is_at_or_over_cap():
    groups = [grp("s1", 1_875, spend=600, results=5), grp("s2", 1_875, spend=900, results=6)]
    assert by_kind(run(groups), "scale") == []
    assert by_kind(run(groups, spent_month=usd(90_000)), "scale") == []


def test_group_changed_within_24_hours_is_not_touched_again():
    groups = [grp("strong", spend=600, results=5, changed_hours=5), grp("weak", spend=700, results=2, changed_hours=5)]
    assert run(groups) == []


def test_group_never_changed_before_is_eligible():
    groups = [grp("strong", spend=600, results=5, changed_hours=0)]
    assert by_kind(run(groups), "scale")


def test_mature_group_with_cpa_equal_to_target_counts_as_strong():
    assert by_kind(run([grp("g", spend=400, results=2)]), "scale")  # 2,00 $ = целевая


# --- выгорание ----------------------------------------------------------------------------

def test_frequency_above_three_warns_even_for_young_groups():
    decisions = run([grp("g1", hours=5, frequency="3.4", name="Широкая")])
    (warning,) = decisions
    assert warning.kind == "fatigue_warning" and warning.new_budget is None and warning.to_action() is None
    assert "3,4" in warning.reason_ru and "3,0" in warning.reason_ru


def test_frequency_exactly_three_is_fine_and_threshold_is_configurable():
    assert run([grp("g1", frequency="3.0")]) == []
    custom = snapshot([grp("g1", frequency="2.6")], rules=OptimizerRules(fatigue_frequency=Decimal("2.5")))
    assert decide(custom, target_cpa=TARGET)


def test_paused_group_does_not_also_get_a_fatigue_warning():
    kinds = [d.kind for d in run([grp("g1", spend=1_000, results=0, frequency="5.0")])]
    assert kinds == ["pause_group"]


# --- сквозные свойства --------------------------------------------------------------------

def test_decisions_are_deterministic_and_pass_through_check_action():
    groups = [grp("weak", spend=700, results=2), grp("strong", spend=600, results=5),
              grp("bad", spend=1_000, results=1)]
    snap = snapshot(groups)
    first, second = decide(snap, target_cpa=TARGET), decide(snap, target_cpa=TARGET)
    assert first == second
    verdicts = check_decisions(first, snap)
    assert len(verdicts) == len(first)
    assert all(verdict == Allowed() for _, verdict in verdicts)  # сумма не растёт — кнопка не нужна


def test_scale_needs_button_in_confirm_mode_and_goes_through_in_autonomous():
    groups = [grp("s1", spend=600, results=5)]
    for mode, expected in (("confirm", NeedsApproval), ("autonomous", Allowed)):
        snap = snapshot(groups, mode=mode)
        decisions = decide(snap, target_cpa=TARGET)
        (decision,) = by_kind(decisions, "scale")
        assert isinstance(check_action(decision.to_action(), snap), expected)
        assert isinstance(dict(check_decisions(decisions, snap))[decision], expected)


@pytest.mark.parametrize("seed", range(60))
def test_rebalancing_alone_never_grows_total_and_scaling_never_exceeds_cap(seed):
    import random

    rng = random.Random(seed)
    groups = [grp(f"g{i}", rng.choice([500, 600, 1_000, 1_500]), spend=rng.choice([0, 300, 500, 900, 2_000]),
                  results=rng.choice([0, 1, 2, 5]), hours=rng.choice([10, 80, 200]),
                  changed_hours=rng.choice([0, 3, 100])) for i in range(rng.randint(1, 5))]
    snap = snapshot(groups, spent_month=usd(rng.choice([0, 40_000, 80_000])))
    decisions = decide(snap, target_cpa=TARGET)
    budgets = {g.id: g.daily_budget.amount_minor for g in groups if g.active}
    after_rebalance = dict(budgets)
    for d in decisions:
        if d.kind == "pause_group":
            after_rebalance[d.target_id] = 0
        elif d.kind == "set_budget":
            after_rebalance[d.target_id] = d.new_budget.amount_minor
    assert sum(after_rebalance.values()) <= sum(budgets.values())
    after_all = dict(after_rebalance)
    for d in by_kind(decisions, "scale"):
        after_all[d.target_id] = d.new_budget.amount_minor
    if sum(after_rebalance.values()) <= snap.daily_cap.amount_minor:
        assert sum(after_all.values()) <= snap.daily_cap.amount_minor
    for d in decisions:
        if d.new_budget is not None:
            old = budgets[d.target_id]
            assert abs(d.new_budget.amount_minor - old) * 100 <= old * 20


# --- темп: подгонка под потолок -------------------------------------------------------------

def test_pace_does_nothing_when_budgets_fit_under_cap():
    assert enforce_pace(snapshot([grp("g1", 1_000), grp("g2", 1_000)])) == []


def test_exhausted_month_pauses_everything_with_explanation():
    decisions = enforce_pace(snapshot([grp("g1"), grp("g2")], spent_month=usd(100_000)))
    assert [d.kind for d in decisions] == ["pause_group", "pause_group"]
    assert all("лимит месяца исчерпан" in d.reason_ru.lower() for d in decisions)


def test_budgets_shrink_proportionally_above_meta_minimum():
    groups = [grp("g1", 1_500), grp("g2", 1_500), grp("g3", 1_000)]
    snap = snapshot(groups, spent_month=usd(100_000 - 48_000))
    assert snap.daily_cap == usd(3_000)
    decisions = enforce_pace(snap)
    new = {d.target_id: d.new_budget for d in decisions}
    assert new == {"g1": usd(1_100), "g2": usd(1_100), "g3": usd(800)}
    assert sum(b.amount_minor for b in new.values()) <= 3_000
    assert all(d.kind == "set_budget" and "$" in d.reason_ru for d in decisions)


def test_reductions_for_cap_pass_check_action_even_when_bigger_than_twenty_percent():
    groups = [grp("g1", 1_500, changed_hours=1), grp("g2", 1_500, changed_hours=1), grp("g3", 1_000)]
    snap = snapshot(groups, spent_month=usd(100_000 - 48_000))
    for decision in enforce_pace(snap):
        assert check_action(decision.to_action(), snap) == Allowed()


def test_weakest_groups_are_paused_when_cap_is_below_sum_of_minimums():
    groups = [grp("good", 1_000, spend=200, results=2), grp("mid", 1_000, spend=600, results=2),
              grp("none", 1_000, spend=500, results=0)]
    snap = snapshot(groups, spent_month=usd(100_000 - 16_000))  # потолок 10,00 $ = 1000, минимумы 3×500
    assert snap.daily_cap == usd(1_000)
    decisions = enforce_pace(snap)
    assert [d.target_id for d in decisions if d.kind == "pause_group"] == ["none"]
    assert {d.target_id: d.new_budget for d in decisions if d.kind == "set_budget"} == {
        "good": usd(500), "mid": usd(500)}


def test_cap_below_one_minimum_pauses_the_company_until_month_end():
    groups = [grp("g1", 1_000), grp("g2", 1_000)]
    snap = snapshot(groups, spent_month=usd(100_000 - 4_000))  # потолок 2,50 $ < минимума 5,00 $
    decisions = enforce_pace(snap)
    assert [d.kind for d in decisions] == ["pause_group", "pause_group"]
    assert all("до конца месяца" in d.reason_ru for d in decisions)
