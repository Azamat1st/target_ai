"""Интересы и оценки аудиторий (R20, R21): в таргетинг попадают только ID из поиска Meta."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from target_ai.agents import Objective
from target_ai.meta.errors import MetaError
from target_ai.policy import InterestInfo

log = logging.getLogger(__name__)

OPTIMIZATION_GOAL = {
    Objective.DIRECT_MESSAGES: "CONVERSATIONS", Objective.LEAD_FORM: "LEAD_GENERATION",
    Objective.WEBSITE_TRAFFIC: "LINK_CLICKS", Objective.PROFILE_TRAFFIC: "PROFILE_VISIT", Objective.REACH: "REACH",
}
NEEDS_PAGE = frozenset({Objective.DIRECT_MESSAGES, Objective.LEAD_FORM, Objective.PROFILE_TRAFFIC, Objective.REACH})
MAX_KEYWORDS = 8
MAX_PER_KEYWORD = 3
MAX_ESTIMATES = 12


def targeting_spec(countries: Iterable[str], interest_ids: Iterable[str] = ()) -> dict:
    spec: dict = {"geo_locations": {"countries": list(countries)}, "publisher_platforms": ["instagram"]}
    ids = [{"id": i} for i in interest_ids]
    if ids:
        spec["flexible_spec"] = [{"interests": ids}]
    return spec


async def estimate_size(gateway, account_id: str, objective: Objective, page_id: str | None,
                        countries: Iterable[str], interest_ids: Iterable[str] = ()) -> int | None:
    """Середина оценки Meta; «неизвестно» (None) — не «пусто»."""
    promoted = {"page_id": page_id} if page_id and objective in NEEDS_PAGE else None
    try:
        bounds = await gateway.estimate_audience(account_id, targeting_spec(countries, interest_ids),
                                                 OPTIMIZATION_GOAL[objective], promoted)
    except MetaError as exc:
        log.info("Оценка аудитории недоступна: %s", exc.kind)
        return None
    return None if bounds is None else (bounds[0] + bounds[1]) // 2


async def build_catalog(gateway, account_id: str, objective: Objective, page_id: str | None,
                        countries: tuple[str, ...], keywords: Iterable[str]) -> tuple[dict[str, tuple[InterestInfo, ...]], list[str]]:
    """Ключевые слова Стратега -> найденные интересы Meta с оценкой размера. Не найденное в каталог не попадает."""
    catalog: dict[str, tuple[InterestInfo, ...]] = {}
    notes: list[str] = []
    estimates = 0
    for keyword in list(dict.fromkeys(k.strip() for k in keywords if k.strip()))[:MAX_KEYWORDS]:
        try:
            rows = await gateway.search_interests(keyword, limit=5)
        except MetaError as exc:
            notes.append(f"Поиск интересов по «{keyword}» не удался: {exc.user_message_ru}")
            continue
        infos = []
        for row in rows[:MAX_PER_KEYWORD]:
            size = None
            if estimates < MAX_ESTIMATES:
                estimates += 1
                size = await estimate_size(gateway, account_id, objective, page_id, countries, [str(row["id"])])
            infos.append(InterestInfo(id=str(row["id"]), name=str(row.get("name", "")), audience_size=size))
        if infos:
            catalog[keyword] = tuple(infos)
        else:
            notes.append(f"Интерес «{keyword}» не найден в Meta — не использую.")
    return catalog, notes
