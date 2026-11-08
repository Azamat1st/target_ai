"""Правила Meta, которые соблюдает фейк (спека §6/§7). Возвращают FakeError или None."""

from __future__ import annotations

from typing import Any

from target_ai.fakemeta import errors
from target_ai.fakemeta.errors import FakeError
from target_ai.fakemeta.state import FakeState
from target_ai.meta.regions import includes_eu

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
    countries = (targeting.get("geo_locations") or {}).get("countries") or []
    if includes_eu(countries) and not (body.get("dsa_beneficiary") and body.get("dsa_payor")):
        return errors.eu_dsa_required()
    return check_daily_budget(account, body.get("daily_budget"), body.get("optimization_goal"))


def validate_creative(state: FakeState, body: dict[str, Any]) -> FakeError | None:
    if not (body.get("object_story_spec") or body.get("object_id")):
        return errors.invalid_parameter("object_story_spec")
    photo = (body.get("object_story_spec") or {}).get("photo_data") or {}
    if photo.get("image_hash") and photo["image_hash"] not in state.data["images"]:
        return errors.invalid_parameter("object_story_spec.photo_data.image_hash")
    media_id = body.get("source_instagram_media_id")
    if media_id and str(media_id) not in state.data["media"]:
        return errors.invalid_parameter("source_instagram_media_id")
    return None


def validate_ad(state: FakeState, account: dict[str, Any], body: dict[str, Any]) -> FakeError | None:
    adset = state.obj(str(body.get("adset_id", "")))
    if adset is None or adset["account_id"] != account["id"]:
        return errors.invalid_parameter("adset_id")
    creative = body.get("creative") or {}
    found = state.obj(str(creative.get("creative_id", ""))) if isinstance(creative, dict) else None
    return None if found is not None else errors.invalid_parameter("creative")


def _event_source_ids(body: dict[str, Any]) -> list[str]:
    inclusions = (body.get("rule") or {}).get("inclusions") or {}
    return [str(src["id"]) for rule in inclusions.get("rules") or [] for src in rule.get("event_sources") or []]


def validate_audience(state: FakeState, body: dict[str, Any]) -> FakeError | None:
    if body.get("subtype") == "ENGAGEMENT" and not _event_source_ids(body):
        return errors.invalid_parameter("rule")
    if body.get("subtype") != "LOOKALIKE":
        return None
    if state.obj(str(body.get("origin_audience_id", ""))) is None:
        return errors.invalid_parameter("origin_audience_id")
    if "country" in (body.get("lookalike_spec") or {}):
        return errors.invalid_parameter("lookalike_spec.country")
    return None


LOOKALIKE_SIZE = 1_000_000


def audience_size(state: FakeState, body: dict[str, Any], object_id: str) -> int:
    """Размер аудитории: вовлечённые - от Instagram-источника (одинаков при каждом создании), прочее - от id."""
    if body.get("subtype") == "LOOKALIKE":
        return LOOKALIKE_SIZE
    sources = _event_source_ids(body)
    if body.get("subtype") == "ENGAGEMENT" and sources:
        ig = state.data["ig"].get(sources[0]) or {}
        return int(ig.get("engaged_size") or 300 + (int(sources[0]) * 7919) % 2000)
    return 1_000 + int(object_id) % 500


def validate_update(state: FakeState, obj: dict[str, Any], account: dict[str, Any], body: dict[str, Any]) -> FakeError | None:
    if body.get("status") == "ACTIVE" and obj["kind"] == "ad" and obj["review"] == "DISAPPROVED":
        return errors.ad_disapproved()
    if "spend_cap" in body and (failure := _check_spend_cap(account, body["spend_cap"])):
        return failure
    if "daily_budget" in body:
        return check_daily_budget(account, body["daily_budget"], obj["params"].get("optimization_goal"))
    return None


def validate_leadform(body: dict[str, Any]) -> FakeError | None:
    policy = body.get("privacy_policy")
    if not isinstance(policy, dict) or not policy.get("url"):
        return errors.invalid_parameter("privacy_policy.url")
    if not body.get("questions"):
        return errors.invalid_parameter("questions")
    return None
