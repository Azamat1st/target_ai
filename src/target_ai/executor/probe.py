"""Проверочный креатив для проб и тестового уровня: ссылается на настоящие объекты, не на выдуманные."""

from __future__ import annotations

from typing import Any

from .spec import standin_creative_params

PROBE_MEDIA_LIMIT = 10
INSTAGRAM_URL = "https://www.instagram.com/"


async def standin_for(gateway, *, name: str, page_id: str, ig_user_id: str, website: str | None = None,
                      cta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Первый пригодный пост IG; постов нет - ссылка на сайт или профиль (без картинки)."""
    posts = await gateway.get_ig_media(ig_user_id, limit=PROBE_MEDIA_LIMIT)
    media = next((p for p in posts if (p.get("boost_eligibility_info") or {}).get("eligible_to_boost", True)
                  and p.get("id")), None)
    link = ""
    if media is None:
        link = website or ""
        if not link:
            profile = await gateway.get_ig_profile(ig_user_id)
            link = profile.get("website") or f"{INSTAGRAM_URL}{profile.get('username', '')}/"
    return standin_creative_params(name=name, page_id=page_id, ig_user_id=ig_user_id,
                                   media_id=str(media["id"]) if media else None, link=link, cta=cta)
