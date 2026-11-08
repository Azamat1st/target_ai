"""Детерминированные пункты Контролёра (спека §3, R06): правила 1, 5, 8, 9 и признак «18+»."""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_TEXT_LENGTH = 2_200
MAX_HEADLINE_LENGTH = 40
MAX_CAPS_SHARE_PCT = 30
MIN_LETTERS_FOR_CAPS = 5
MIN_LETTERS_FOR_LANGUAGE = 5

_P = re.IGNORECASE
_PERSONAL = tuple(
    re.compile(pattern, _P)
    for pattern in (
        r"\b(?:вы|ты)\s+(?:\w+\s+)?(?:страдаете|страдаешь|болеете|болеешь|боретесь|борешься|"
        r"толст\w*|одинок\w*|бедн\w*|безработн\w*|курите|куришь|лыс\w*|стесняетесь|стесняешься|"
        r"мусульман\w*|христиан\w*|верующ\w*|атеист\w*|судим\w*|гей|лесбиянк\w*)",
        r"\bу\s+(?:вас|тебя)\s+(?:\w+\s+)?(?:лишн\w+\s+вес|ожирени\w*|депресси\w*|диабет\w*|долг\w*|"
        r"кредит\w*|проблем\w*\s+с|акне|прыщ\w*|морщин\w*|целлюлит\w*)",
        r"\bуста(?:ли|л|ла)\s+от\s+(?:долг|кредит|одиночеств|лишн|болезн|боли|прыщ)\w*",
        r"\b(?:вам|тебе)\s+(?:уже\s+)?(?:за\s+\d+|\d+\+)",
        r"\bваш(?:е|а|и|у|им|его)?\s+(?:диагноз|вес|долг\w*|болезн\w*|возраст)",
        r"\bare you (?:suffering|overweight|in debt|lonely|depressed)",
        r"\byou(?:'re| are) (?:overweight|in debt|lonely|depressed)",
    )
)
_EXCLAIM = re.compile(r"[!?]{3,}")
_ADULT = re.compile(
    r"алкогол|\bпив(?:о|а|у|е|ом)\b|\bвин(?:о|а|у|е|ом)\b|виски|водк|коньяк|знакомств|"
    r"\bбад(?:ы|ов|ам|ах)?\b|похуден|снижени\w+ веса|крипто|биткоин|рецептурн|табак|вейп|"
    r"сигарет|кальян",
    _P,
)
_CYRILLIC_LANGS = frozenset({"ru", "uk", "be", "bg", "kk", "ky", "sr", "mk", "mn", "tg"})
_LATIN_LANGS = frozenset({"en", "de", "es", "fr", "it", "pt", "pl", "tr", "uz", "nl", "cs", "ro", "az"})


@dataclass(frozen=True)
class ControllerFinding:
    rule: int  # номер пункта чек-листа Контролёра из спеки §3
    message_ru: str


@dataclass(frozen=True)
class ControllerReport:
    findings: tuple[ControllerFinding, ...]
    requires_adult: bool  # ограниченный контент: код ставит age_min=18

    @property
    def ok(self) -> bool:
        return not self.findings


def _letters(text: str) -> list[str]:
    return [ch for ch in text if ch.isalpha()]


def _is_cyrillic(ch: str) -> bool:
    return "Ѐ" <= ch <= "ӿ"


def _is_latin(ch: str) -> bool:
    return ch.isascii() and ch.isalpha() or "À" <= ch <= "ɏ"


def _language_finding(text: str, language: str | None) -> ControllerFinding | None:
    code = (language or "").lower().split("-")[0]
    letters = _letters(text)
    if code not in _CYRILLIC_LANGS | _LATIN_LANGS or len(letters) < MIN_LETTERS_FOR_LANGUAGE:
        return None
    expected = _is_cyrillic if code in _CYRILLIC_LANGS else _is_latin
    if sum(1 for ch in letters if expected(ch)) * 2 >= len(letters):
        return None
    return ControllerFinding(9, f"Язык текста не совпадает с языком компании ({code}).")


def _caps_finding(text: str) -> ControllerFinding | None:
    letters = _letters(text)
    if len(letters) < MIN_LETTERS_FOR_CAPS:
        return None
    caps = sum(1 for ch in letters if ch.isupper())
    if caps * 100 > len(letters) * MAX_CAPS_SHARE_PCT:
        return ControllerFinding(
            5, f"Слишком много заглавных букв: {caps * 100 // len(letters)}% при допустимых "
               f"{MAX_CAPS_SHARE_PCT}%.")
    return None


def controller_check(text: str, *, headline: str = "", language: str | None = None) -> ControllerReport:
    """Детерминированная часть проверки Контролёра; смысловые пункты остаются за моделью."""
    normalized = text.replace("ё", "е").replace("Ё", "Е")
    findings: list[ControllerFinding] = []
    if any(pattern.search(normalized) for pattern in _PERSONAL):
        findings.append(ControllerFinding(
            1, "Текст обращается к личным характеристикам читателя (здоровье, деньги, возраст…)."))
    caps = _caps_finding(text)
    if caps:
        findings.append(caps)
    if _EXCLAIM.search(text):
        findings.append(ControllerFinding(5, "Повторяющиеся «!» и «?» выглядят как манипуляция."))
    if len(text) > MAX_TEXT_LENGTH:
        findings.append(ControllerFinding(
            8, f"Текст длиннее {MAX_TEXT_LENGTH} символов: {len(text)}."))
    if len(headline) > MAX_HEADLINE_LENGTH:
        findings.append(ControllerFinding(
            8, f"Заголовок длиннее {MAX_HEADLINE_LENGTH} символов: {len(headline)}."))
    language_finding = _language_finding(text, language)
    if language_finding:
        findings.append(language_finding)
    return ControllerReport(tuple(findings), bool(_ADULT.search(normalized)))
