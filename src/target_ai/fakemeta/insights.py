"""Инсайты и оценки аудитории фейка в форме ответов Graph API."""

from __future__ import annotations

import math
from typing import Any

from target_ai.fakemeta.data import INTERESTS
from target_ai.fakemeta.state import FakeState
from target_ai.money import offset

DEFAULT_POPULATION = 20_000_000
NARROW_FACTOR = 0.6
RESULT_ACTIONS: dict[str, str | None] = {
    "CONVERSATIONS": "onsite_conversion.messaging_conversation_started_7d",
    "LEAD_GENERATION": "onsite_conversion.lead_grouped",
    "LINK_CLICKS": "link_click",
    "PROFILE_VISIT": None,
    "REACH": None,
}
REACH_UNIT = 1000


def money_str(minor: float, currency: str) -> str:
    unit = offset(currency)
    digits = len(str(unit)) - 1
    return f"{minor / unit:.{digits}f}"


def _scope_contains(state: FakeState, scope_id: str, obj: dict[str, Any]) -> bool:
    if scope_id.startswith("act_"):
        return obj["account_id"] == scope_id
    return scope_id in (obj["id"], obj.get("parent_id"), obj.get("campaign_id"), obj.get("adset_id"))


def build_rows(state: FakeState, scope_id: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    level = params.get("level", "adset")
    window = params.get("time_range") or {}
    since, until = window.get("since", "0000-01-01"), window.get("until", "9999-12-31")
    rows: list[dict[str, Any]] = []
    for obj in state.objects(level):
        if not _scope_contains(state, scope_id, obj):
            continue
        account = state.data["accounts"][obj["account_id"]]
        for day, stat in sorted(state.data["stats"].get(obj["id"], {}).items()):
            if since <= day <= until:
                rows.append(_row(state, obj, account, day, stat))
    return sorted(rows, key=lambda r: (r["date_start"], r.get("ad_id") or r.get("adset_id")))


def _row(state: FakeState, obj: dict[str, Any], account: dict[str, Any], day: str, stat: dict[str, Any]) -> dict[str, Any]:
    currency, spend, goal = account["currency"], stat["spend_minor"], stat["goal"]
    impressions, clicks = stat["impressions"], stat["clicks"]
    results, action = stat["results"], RESULT_ACTIONS.get(goal)
    row: dict[str, Any] = {
        "date_start": day, "date_stop": day, "account_id": account["account_id"],
        "campaign_id": obj.get("campaign_id") or obj.get("parent_id"),
        "spend": money_str(spend, currency), "impressions": str(impressions), "reach": str(stat["reach"]),
        "frequency": f"{stat['frequency']:.4f}", "clicks": str(clicks), "inline_link_clicks": str(stat["link_clicks"]),
        "ctr": f"{(clicks / impressions * 100) if impressions else 0:.6f}",
    }
    row["adset_id" if obj["kind"] == "adset" else "ad_id"] = obj["id"]
    row["adset_name" if obj["kind"] == "adset" else "ad_name"] = obj["params"].get("name", "")
    if obj["kind"] == "ad":
        row["adset_id"] = obj.get("adset_id") or obj.get("parent_id")
    row.update(_results_fields(goal, action, spend, results, stat, currency))
    return row


def _results_fields(goal: str, action: str | None, spend: int, results: int, stat: dict[str, Any], currency: str) -> dict[str, Any]:
    actions = [{"action_type": "link_click", "value": str(stat["link_clicks"])}]
    cost_actions = []
    if action and action != "link_click":
        actions.append({"action_type": action, "value": str(results)})
    if results:
        cost_actions.append({"action_type": action or "link_click",
                             "value": money_str(spend / results, currency)})
    indicator = f"actions:{action}" if action else ("reach" if goal == "REACH" else "profile_visit")
    per_result = spend / results if results else 0
    if goal == "REACH" and results:
        per_result = spend / results * REACH_UNIT
    return {
        "actions": actions, "cost_per_action_type": cost_actions,
        "results": [{"indicator": indicator, "values": [{"value": str(results), "attribution_windows": ["default"]}]}],
        "cost_per_result": [{"indicator": indicator, "values": [{"value": money_str(per_result, currency)}]}] if results else [],
    }


def estimate_bounds(targeting: dict[str, Any]) -> tuple[int, int]:
    """Грубая оценка: размер самого узкого интереса, сужения уменьшают аудиторию."""
    sizes = {name: size for name, _, size in INTERESTS}
    named = [
        sizes[i["name"]] for spec in targeting.get("flexible_spec", []) for i in spec.get("interests", [])
        if i.get("name") in sizes
    ]
    population = min(named) if named else DEFAULT_POPULATION
    if targeting.get("genders"):
        population = int(population * NARROW_FACTOR)
    age_min, age_max = int(targeting.get("age_min", 18)), int(targeting.get("age_max", 65))
    population = int(population * min(1.0, (age_max - age_min + 1) / 48))
    high = max(1, population // 100 * 100)
    return math.floor(high * 0.8), high
