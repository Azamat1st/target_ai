"""Предзаполнение демо: аккаунты, страницы, Instagram, медиа, чужие кампании."""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

from target_ai.fakemeta import data
from target_ai.fakemeta.state import FakeState
from target_ai.money import from_major

MEDIA_PER_ACCOUNT = 6
FOREIGN_DAILY_BUDGET_MAJOR = 20
SEED = 20261108


def seed_demo(state: FakeState) -> None:
    if state.data["accounts"]:
        return  # уже предзаполнено (демо переживает перезапуск)
    rng = random.Random(SEED)
    for spec in data.DEMO_ACCOUNTS:
        account = _add_account(state, spec)
        page_id, ig_id = _add_page_and_ig(state, spec, account["id"], rng)
        _add_media(state, ig_id, spec["ig"], rng)
        _add_foreign_campaign(state, account, page_id)
    for rival in data.COMPETITORS:
        ig_id = state.new_id()
        state.data["ig"][ig_id] = _ig_row(ig_id, rival["username"], rival["name"], rival["followers"], rival["media"])
        _add_media(state, ig_id, rival["username"], rng)
    state.save()


def _add_account(state: FakeState, spec: dict[str, Any]) -> dict[str, Any]:
    imp, high, low = spec["budgets"]
    account = {
        "id": f"act_{spec['num']}", "account_id": spec["num"], "name": spec["name"], "currency": spec["currency"],
        "timezone_name": spec["tz"], "account_status": 1, "disable_reason": 0, "funding_source": f"FS_{spec['num']}",
        "min_campaign_group_spend_cap": str(spec["spend_cap_min"]), "amount_spent": "0",
        "minimum_budgets": {"min_daily_budget_imp": str(imp), "min_daily_budget_high_freq": str(high),
                            "min_daily_budget_low_freq": str(low)},
    }
    state.data["accounts"][account["id"]] = account
    return account


def _ig_row(ig_id: str, username: str, name: str, followers: int, media_count: int) -> dict[str, Any]:
    return {
        "id": ig_id, "username": username, "name": name, "biography": f"{name}. Официальный аккаунт.",
        "website": f"https://{username.replace('_', '-')}.example", "followers_count": followers,
        "follows_count": 300, "media_count": media_count,
    }


def _add_page_and_ig(state: FakeState, spec: dict[str, Any], account_id: str, rng: random.Random) -> tuple[str, str]:
    ig_id = state.new_id()
    state.data["ig"][ig_id] = {
        **_ig_row(ig_id, spec["ig"], spec["page"], rng.randint(2_000, 30_000), MEDIA_PER_ACCOUNT),
        "engaged_size": spec["engaged"],  # размер аудитории вовлечённых: у одних IG больше 1000, у других меньше
    }
    page_id = state.new_id()
    state.data["pages"][page_id] = {
        "id": page_id, "name": spec["page"], "instagram_business_account": {"id": ig_id},
        "access_token": f"fake-page-token-{page_id}", "account_id": account_id,
    }
    return page_id, ig_id


def _add_media(state: FakeState, ig_id: str, username: str, rng: random.Random) -> None:
    for index in range(MEDIA_PER_ACCOUNT):
        media_id = state.new_id()
        reels = index % 3 == 2
        likes = rng.randint(40, 900)
        reach = likes * rng.randint(8, 20)
        state.data["media"][media_id] = {
            "id": media_id, "ig_id": ig_id, "caption": f"Пост {index + 1} от {username}: делимся новостями.",
            "like_count": likes, "comments_count": rng.randint(2, 60), "media_type": "VIDEO" if reels else "IMAGE",
            "media_product_type": "REELS" if reels else "FEED",
            "permalink": f"https://www.instagram.com/p/{media_id}/", "timestamp": f"2026-10-{index + 1:02d}T10:00:00+0000",
            "media_url": f"https://cdn.example/{media_id}.jpg", "thumbnail_url": f"https://cdn.example/{media_id}_t.jpg",
            "boost_eligibility_info": {"eligible_to_boost": True},
            "insights": {"reach": reach, "saved": likes // 6, "shares": likes // 9,
                         "total_interactions": likes + 20, "views": reach * 2},
        }


def _add_foreign_campaign(state: FakeState, account: dict[str, Any], page_id: str) -> None:
    budget = from_major(Decimal(FOREIGN_DAILY_BUDGET_MAJOR), account["currency"]).amount_minor
    campaign = state.add_object(
        "campaign", account["id"], {"name": "Чужая кампания (не трогать)", "objective": "OUTCOME_TRAFFIC"},
        status="ACTIVE", foreign=True,
    )
    state.add_object(
        "adset", account["id"], {"name": "Чужая группа", "promoted_object": {"page_id": page_id}},
        status="ACTIVE", foreign=True, parent_id=campaign["id"], campaign_id=campaign["id"], daily_budget=budget,
    )
