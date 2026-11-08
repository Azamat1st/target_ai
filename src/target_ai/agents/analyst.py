"""Аналитик: паспорт компании. Сильная модель + веб-инструменты + vision."""

import base64
import json
from typing import Any

from target_ai.llm import LLM, wrap_untrusted

from ._runner import dump, message, untrusted_json
from .inputs import AnalystInput
from .prompts import ANALYST
from .schemas import Passport

SEARCH_MAX_USES = 5
FETCH_MAX_USES = 8
TOOLS: tuple[dict[str, Any], ...] = (
    {"type": "web_search_20260209", "name": "web_search", "max_uses": SEARCH_MAX_USES},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": FETCH_MAX_USES},
)


def _blocks(data: AnalystInput) -> list[dict[str, Any]]:
    trusted = {
        "questionnaire": dump(data.questionnaire),
        "competitor_urls": list(data.competitor_urls),
        "history": list(data.history),
        "known_user_facts": dump(data.user_facts),
    }
    untrusted = []
    if data.external_facts:
        untrusted.append(untrusted_json("known_facts_external", data.external_facts))
    if data.ig_profile is not None:
        untrusted.append(wrap_untrusted("instagram_profile", json.dumps(dict(data.ig_profile), ensure_ascii=False, default=str)))
    if data.ig_posts:
        untrusted.append(wrap_untrusted("instagram_posts", json.dumps([dict(p) for p in data.ig_posts], ensure_ascii=False, default=str)))
    blocks: list[dict[str, Any]] = [{"type": "text", "text": message(trusted, *untrusted)}]
    for shot in data.screenshots:
        encoded = base64.standard_b64encode(shot.data).decode("ascii")
        blocks.append({"type": "text", "text": '<untrusted_data source="screenshot">'})
        blocks.append(
            {"type": "image", "source": {"type": "base64", "media_type": shot.media_type, "data": encoded}}
        )
        blocks.append({"type": "text", "text": "</untrusted_data>"})
    return blocks


async def analyze(llm: LLM, data: AnalystInput) -> Passport:
    return await llm.structured("strong", ANALYST, _blocks(data), Passport, tools=list(TOOLS))
