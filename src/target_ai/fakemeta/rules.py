"""Правила Meta, которые соблюдает фейк (спека §6/§7). Возвращают FakeError или None."""

from __future__ import annotations

from typing import Any

from target_ai.fakemeta import errors
from target_ai.fakemeta.errors import FakeError
from target_ai.fakemeta.state import FakeState

ADVANTAGE_PATH = "targeting.targeting_automation.advantage_audience"
IMPRESSION_GOALS = frozenset({"REACH", "IMPRESSIONS"})
NARROWING_KEYS = ("genders", "flexible_spec", "exclusions", "custom_audiences", "interests", "behaviors")
BROAD_AGE_MAX = 65


def _minor(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_narrow(targeting: dict[str, Any]) -> bool:
    if any(targeting.get(key) for key in NARROWING_KEYS):
        return True
    age_max = _minor(targeting.get("age_max"))
    return age_max is not None and age_max < BROAD_AGE_MAX


def validate_campaign(account: dict[str, Any], body: dict[str, Any]) -> FakeError | None:
    if "special_ad_categories" not in body:
        return errors.invalid_parameter("special_ad_categories")
    return _check_spend_cap(account, body.get("spend_cap"))


def _check_spend_cap(account: dict[str, Any], value: Any) -> FakeError | None:
    cap = _minor(value)
    if cap is not None and cap < int(account["min_campaign_group_spend_cap"]):
        return errors.spend_cap_too_low()
    return None


def check_daily_budget(account: dict[str, Any], value: Any, goal: str | None) -> FakeError | None:
    budget = _minor(value)
    if budget is None:
        return None
    key = "min_daily_budget_imp" if goal in IMPRESSION_GOALS else "min_daily_budget_high_freq"
    return errors.budget_too_low() if budget < int(account["minimum_budgets"][key]) else None


def validate_adset(state: FakeState, account: dict[str, Any], body: dict[str, Any]) -> FakeError | None:
    campaign = state.obj(str(body.get("campaign_id", "")))
    if campaign is None or campaign["account_id"] != account["id"]:
        return errors.invalid_parameter("campaign_id")
    targeting = body.get("targeting")
    if not isinstance(targeting, dict):
        return errors.invalid_parameter("targeting")
    automation = targeting.get("targeting_automation") or {}
    if _is_narrow(targeting) and "advantage_audience" not in automation:
        return errors.invalid_parameter(ADVANTAGE_PATH)
    return check_daily_budget(account, body.get("daily_budget"), body.get("optimization_goal"))


def validate_creative(body: dict[str, Any]) -> FakeError | None:
    if not (body.get("object_story_spec") or body.get("object_id")):
        return errors.invalid_parameter("object_story_spec")
    return None


def validate_ad(state: FakeState, account: dict[str, Any], body: dict[str, Any]) -> FakeError | None:
    adset = state.obj(str(body.get("adset_id", "")))
    if adset is None or adset["account_id"] != account["id"]:
        return errors.invalid_parameter("adset_id")
    creative = body.get("creative") or {}
    found = state.obj(str(creative.get("creative_id", ""))) if isinstance(creative, dict) else None
    return None if found is not None else errors.invalid_parameter("creative")


def validate_audience(state: FakeState, body: dict[str, Any]) -> FakeError | None:
    if body.get("subtype") != "LOOKALIKE":
        return None
    if state.obj(str(body.get("origin_audience_id", ""))) is None:
        return errors.invalid_parameter("origin_audience_id")
    if "country" in (body.get("lookalike_spec") or {}):
        return errors.invalid_parameter("lookalike_spec.country")
    return None


def validate_update(state: FakeState, obj: dict[str, Any], account: dict[str, Any], body: dict[str, Any]) -> FakeError | None:
    if body.get("status") == "ACTIVE" and obj["kind"] == "ad" and obj["review"] == "DISAPPROVED":
        return errors.ad_disapproved()
    if "spend_cap" in body and (failure := _check_spend_cap(account, body["spend_cap"])):
        return failure
    if "daily_budget" in body:
        return check_daily_budget(account, body["daily_budget"], obj["params"].get("optimization_goal"))
    return None
