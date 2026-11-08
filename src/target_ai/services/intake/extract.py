"""Детерминированное извлечение полей анкеты из свободного текста (работает и без Claude)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from target_ai.agents.schemas import Objective

from .geo import Place, find_place

HANDLE_RE = re.compile(r"^@?([A-Za-z0-9._]{1,30})$")
_HANDLE_IN_TEXT = re.compile(r"@([A-Za-z0-9._]{1,30})")
_URL = re.compile(r"https?://[^\s,]+", re.IGNORECASE)
_CURRENCY = (r"долл\w*|usd|eur|евро|руб\w*|rub|[$€£₽¥₺₸]|jpy|[иий]ен\w*|фунт\w*|gbp|тенге|kzt|тг|сум\w*|uzs|лир\w*")
AMOUNT_RE = re.compile(
    rf"[$€£₽¥₺₸]?\s*\d[\d  .,]*\d(?:\s*(?:тыс\w*|млн))?(?:\s*(?:{_CURRENCY}))?"
    rf"|[$€£₽¥₺₸]?\s*\d(?:\s*(?:тыс\w*|млн|[кk](?![а-яa-z])))?(?:\s*(?:{_CURRENCY}))?",
    re.IGNORECASE,
)
_RESULT = r"(?:результат\w*|заяв\w*|лид\w*|сообщени\w*|клик\w*|подписчик\w*)"
CPA_RE = re.compile(
    rf"(?:цен\w*\s+{_RESULT}|стоимост\w*\s+{_RESULT}|cpa|{_RESULT}\s+по)\s*(?:до|около|не\s+более|не\s+больше|в\s+пределах)?\s*"
    rf"(?P<amt>{AMOUNT_RE.pattern})", re.IGNORECASE)
_OBJECTIVE_RULES = (
    (Objective.PROFILE_TRAFFIC, r"профил"),
    (Objective.LEAD_FORM, r"заявк|лид|анкет"),
    (Objective.DIRECT_MESSAGES, r"сообщени|директ|direct|переписк"),
    (Objective.REACH, r"охват|узнаваем"),
    (Objective.WEBSITE_TRAFFIC, r"трафик|сайт|переход"),
)
OBJECTIVE_TITLES = {
    Objective.DIRECT_MESSAGES: "Сообщения в Direct", Objective.LEAD_FORM: "Заявки (лид-форма)",
    Objective.WEBSITE_TRAFFIC: "Трафик на сайт", Objective.PROFILE_TRAFFIC: "Трафик в профиль",
    Objective.REACH: "Охват",
}
MAX_AMOUNT_FRAGMENT = 60


@dataclass(frozen=True)
class Extracted:
    handle: str | None = None
    website: str | None = None
    place: Place | None = None
    objective: Objective | None = None
    amount_text: str | None = None
    cpa_text: str | None = None


def parse_objective(text: str) -> Objective | None:
    low = text.lower()
    for objective, pattern in _OBJECTIVE_RULES:
        if re.search(pattern, low):
            return objective
    return None


def find_amount_fragment(text: str) -> str | None:
    """Короткий фрагмент с суммой (≤ 60 символов) для `parse_amount_ru`; номера вроде @shop2024 и ссылки вырезаются."""
    cleaned = _URL.sub(" ", _HANDLE_IN_TEXT.sub(" ", text))
    match = AMOUNT_RE.search(cleaned)
    fragment = match.group(0).strip(" ,.") if match else ""
    return fragment if fragment and len(fragment) <= MAX_AMOUNT_FRAGMENT else None


def split_cpa(text: str) -> tuple[str | None, str]:
    """Желаемая цена результата («цена заявки до 3 евро») и текст без неё — чтобы не принять её за бюджет."""
    match = CPA_RE.search(text)
    if not match:
        return None, text
    fragment = match.group("amt").strip(" ,.")
    return (fragment if len(fragment) <= MAX_AMOUNT_FRAGMENT else None), text[: match.start()] + " " + text[match.end():]


def extract_fields(text: str) -> Extracted:
    cpa_text, text = split_cpa(text)
    handle = _HANDLE_IN_TEXT.search(text)
    site = _URL.search(text)
    return Extracted(
        handle=handle.group(1) if handle else None,
        website=site.group(0).rstrip(".") if site else None,
        place=find_place(text),
        objective=parse_objective(text),
        amount_text=find_amount_fragment(text),
        cpa_text=cpa_text,
    )
