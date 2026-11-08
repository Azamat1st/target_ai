"""Состав запуска (из `Plan.data`) и параметры объектов Meta по цели (спека §6)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from target_ai.money import Money

STANDARD_ENHANCEMENTS = {"creative_features_spec": {"standard_enhancements": {"enroll_status": "OPT_OUT"}}}
POSITIONS_DIRECT = ("stream", "story")
POSITIONS_WIDE = ("stream", "story", "reels")


@dataclass(frozen=True)
class Objective:
    meta_objective: str
    goal: str
    destination: str | None
    promoted_page: bool
    cta: str | None
    positions: tuple[str, ...]
    goal_kind: str


OBJECTIVES: dict[str, Objective] = {
    "direct_messages": Objective("OUTCOME_ENGAGEMENT", "CONVERSATIONS", "INSTAGRAM_DIRECT", True,
                                 "INSTAGRAM_MESSAGE", POSITIONS_DIRECT, "high_freq"),
    "lead_form": Objective("OUTCOME_LEADS", "LEAD_GENERATION", "ON_AD", True, "SIGN_UP",
                           POSITIONS_DIRECT, "high_freq"),
    "website_traffic": Objective("OUTCOME_TRAFFIC", "LINK_CLICKS", "WEBSITE", False, "LEARN_MORE",
                                 POSITIONS_WIDE, "high_freq"),
    "profile_traffic": Objective("OUTCOME_TRAFFIC", "PROFILE_VISIT", "INSTAGRAM_PROFILE", True,
                                 "VIEW_INSTAGRAM_PROFILE", POSITIONS_WIDE, "high_freq"),
    "reach": Objective("OUTCOME_AWARENESS", "REACH", None, True, None, POSITIONS_WIDE, "imp"),
}


@dataclass(frozen=True)
class AdSpec:
    kind: str  # existing_post | new_creative
    creative_id: str  # existing_post: id медиа Instagram; new_creative: id MediaAsset
    text: str = ""
    headline: str = ""


@dataclass(frozen=True)
class GroupSpec:
    name: str
    hypothesis: str
    daily_budget: Money
    goal_kind: str
    advantage_audience: int
    ads: tuple[AdSpec, ...]
    interests: tuple[Mapping[str, str], ...] = ()
    exclude_retargeting: bool = False
    countries: tuple[str, ...] = ()
    radius_km: int | None = None
    age_min: int = 18
    age_max: int = 65
    adult_only: bool = False

    @property
    def needs_audience(self) -> bool:
        return self.hypothesis in ("retargeting", "lookalike")


@dataclass(frozen=True)
class LaunchSpec:
    name: str
    objective: str
    groups: tuple[GroupSpec, ...]
    special_category: str | None = None
    lead_form_id: str | None = None
    website_url: str | None = None

    @property
    def meta(self) -> Objective:
        return OBJECTIVES[self.objective]


def _money(raw: Mapping[str, Any]) -> Money:
    return Money(int(raw["amount_minor"]), str(raw["currency"]))


def money_json(value: Money) -> dict[str, Any]:
    return {"amount_minor": value.amount_minor, "currency": value.currency}


def parse_spec(data: Mapping[str, Any]) -> LaunchSpec:
    """`Plan.data` → состав запуска. Неверные данные — ValueError с русским текстом."""
    try:
        objective = str(data["objective"])
        if objective not in OBJECTIVES:
            raise ValueError(f"Цель «{objective}» не поддерживается.")
        groups = tuple(_group(g) for g in data["groups"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"В медиаплане не хватает данных для запуска: {exc}.") from exc
    if not groups:
        raise ValueError("В медиаплане нет групп.")
    return LaunchSpec(
        name=str(data.get("name") or "Кампания"), objective=objective, groups=groups,
        special_category=data.get("special_category") or None,
        lead_form_id=data.get("lead_form_id"), website_url=data.get("website_url"),
    )


def _group(raw: Mapping[str, Any]) -> GroupSpec:
    ads = tuple(AdSpec(str(a["kind"]), str(a["creative_id"]), str(a.get("text", "")), str(a.get("headline", "")))
                for a in raw["ads"])
    if not ads:
        raise ValueError("У группы нет объявлений.")
    return GroupSpec(
        name=str(raw.get("name") or raw["hypothesis"]), hypothesis=str(raw["hypothesis"]),
        daily_budget=_money(raw["daily_budget"]), goal_kind=str(raw["goal_kind"]),
        advantage_audience=int(raw["advantage_audience"]), ads=ads,
        interests=tuple({"id": str(i["id"]), "name": str(i.get("name", ""))} for i in raw.get("interests", ())),
        exclude_retargeting=bool(raw.get("exclude_retargeting")), countries=tuple(raw.get("countries", ())),
        radius_km=raw.get("radius_km"), age_min=int(raw.get("age_min", 18)), age_max=int(raw.get("age_max", 65)),
        adult_only=bool(raw.get("adult_only")),
    )


def plan_data_from_validated(plan: Any, *, name: str, lead_form_id: str | None = None,
                             website_url: str | None = None) -> dict[str, Any]:
    """`policy.ValidatedPlan` → `Plan.data`, который читает Исполнитель."""
    category = plan.special_category.value if plan.special_category is not None else None
    groups = []
    for g in plan.groups:
        groups.append({
            "name": g.hypothesis, "hypothesis": g.hypothesis, "daily_budget": money_json(g.daily_budget),
            "goal_kind": g.goal_kind, "advantage_audience": g.advantage_audience,
            "interests": [{"id": i.id, "name": i.name} for i in g.interests],
            "exclude_retargeting": g.exclude_retargeting, "countries": list(g.countries),
            "radius_km": g.radius_km, "age_min": g.age_min, "age_max": g.age_max, "adult_only": g.adult_only,
            "ads": [{"kind": a.kind, "creative_id": a.creative_id, "text": a.text, "headline": a.headline}
                    for a in g.ads],
        })
    return {"name": name, "objective": plan.objective, "special_category": category, "groups": groups,
            "lead_form_id": lead_form_id, "website_url": website_url}


def step_names(spec: LaunchSpec) -> list[str]:
    """Шаги саги по порядку (спека §4)."""
    names = ["retire_previous", "campaign"]
    names += [f"audience:{n}" for n, g in enumerate(spec.groups, 1) if g.needs_audience]
    names += [f"adset:{n}" for n in range(1, len(spec.groups) + 1)]
    media = sorted({a.creative_id for g in spec.groups for a in g.ads if a.kind == "new_creative"})
    names += [f"media:{m}" for m in media]
    for n, g in enumerate(spec.groups, 1):
        names += [f"creative:{n}:{m}" for m in range(1, len(g.ads) + 1)]
    for n, g in enumerate(spec.groups, 1):
        names += [f"ad:{n}:{m}" for m in range(1, len(g.ads) + 1)]
    return [*names, "activate"]


def tag_for(install_id: str, launch_id: int, step: str) -> str:
    """Тег в имени объекта Meta; `install_id` отличает объекты этой установки от чужих с теми же номерами."""
    return f"[ta:{install_id}:L{launch_id}:{step}]"


def campaign_params(spec: LaunchSpec, name: str, spend_cap: Money, countries: tuple[str, ...]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "name": name, "objective": spec.meta.meta_objective, "status": "PAUSED", "spend_cap": spend_cap,
        "special_ad_categories": [spec.special_category] if spec.special_category else [],
        "buying_type": "AUCTION", "is_adset_budget_sharing_enabled": False,
    }
    if spec.special_category:
        params["special_ad_category_country"] = list(countries)
    return params


def targeting_dict(spec: LaunchSpec, group: GroupSpec, *, audiences: Mapping[str, str]) -> dict[str, Any]:
    geo: dict[str, Any] = {"countries": list(group.countries)}
    targeting: dict[str, Any] = {
        "geo_locations": geo, "age_min": group.age_min, "age_max": group.age_max,
        "publisher_platforms": ["instagram"], "device_platforms": ["mobile"],
        "instagram_positions": list(spec.meta.positions),
        "targeting_automation": {"advantage_audience": group.advantage_audience},
    }
    if group.interests:
        targeting["flexible_spec"] = [{"interests": [dict(i) for i in group.interests]}]
    if group.needs_audience and audiences.get("own"):
        targeting["custom_audiences"] = [{"id": audiences["own"]}]
    if group.exclude_retargeting and audiences.get("retargeting"):
        targeting["excluded_custom_audiences"] = [{"id": audiences["retargeting"]}]
    return targeting


def adset_params(spec: LaunchSpec, group: GroupSpec, *, name: str, campaign_id: str, page_id: str | None,
                 targeting: dict[str, Any]) -> dict[str, Any]:
    meta = spec.meta
    params: dict[str, Any] = {
        "name": name, "campaign_id": campaign_id, "status": "PAUSED", "daily_budget": group.daily_budget,
        "billing_event": "IMPRESSIONS", "optimization_goal": meta.goal, "targeting": targeting,
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
    }
    if meta.destination:
        params["destination_type"] = meta.destination
    if meta.promoted_page and page_id:
        params["promoted_object"] = {"page_id": page_id}
    return params


def _cta(spec: LaunchSpec) -> dict[str, Any] | None:
    cta = spec.meta.cta
    if cta is None:
        return None
    value: dict[str, Any] = {}
    if spec.lead_form_id:
        value["lead_gen_form_id"] = spec.lead_form_id
    if spec.website_url:
        value["link"] = spec.website_url
    return {"type": cta, "value": value} if value else {"type": cta}


def creative_params(spec: LaunchSpec, ad: AdSpec, *, name: str, page_id: str, ig_user_id: str,
                    image_hash: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {"name": name, "degrees_of_freedom_spec": STANDARD_ENHANCEMENTS}
    cta = _cta(spec)
    if ad.kind == "existing_post":
        params.update(object_id=page_id, instagram_user_id=ig_user_id, source_instagram_media_id=ad.creative_id)
        if cta:
            params["call_to_action"] = cta
        return params
    photo: dict[str, Any] = {"image_hash": image_hash, "caption": ad.text}
    if ad.headline:
        photo["name"] = ad.headline
    if cta:
        photo["call_to_action"] = cta
    params["object_story_spec"] = {"page_id": page_id, "instagram_user_id": ig_user_id, "photo_data": photo}
    return params
