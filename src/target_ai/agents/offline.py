"""Офлайн-реализации агентов (OfflineLLM): детерминированные, без сети.

Каждый обработчик получает JSON задачи (`<task_input>`) и возвращает объект схемы.
Недоверенные блоки сюда не попадают вовсе — поэтому инструкции в них ничего не меняют.
"""

import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from target_ai.llm import DEMO_MARK

from .schemas import (
    NOT_FOUND,
    AdCopySet,
    AdVariant,
    AdVerdict,
    ChecklistItem,
    ComplianceVerdict,
    GroupProposal,
    Hypothesis,
    Intent,
    IntentKind,
    MediaPlanProposal,
    Objective,
    OptimizerOutput,
    Passport,
    Report,
    ReportSection,
    Segment,
    SpecialCategory,
    Violation,
    Voice,
    Explanation,
)

HEADLINE_MAX = 40
CAPS_SHARE_MAX = 0.3
TEXT_MAX = 2200
SHARES_TWO = ("0.5", "0.5")
SHARES_WITH_RETARGETING = ("0.4", "0.4", "0.2")
MIN_GROUPS_WITH_RETARGETING = 3
CTA_BY_OBJECTIVE = {
    "direct_messages": "INSTAGRAM_MESSAGE",
    "lead_form": "SIGN_UP",
    "website_traffic": "LEARN_MORE",
    "profile_traffic": "VIEW_INSTAGRAM_PROFILE",
    "reach": "NO_BUTTON",
}
Handler = Callable[[dict[str, Any]], BaseModel]


def _mark(text: str) -> str:
    return f"{text} {DEMO_MARK}"


def passport(task: dict[str, Any]) -> Passport:
    q = task.get("questionnaire", {})
    offer_note = q.get("offer_note")
    untrusted = task.get("_untrusted", {})
    facts = [*task.get("known_user_facts", []), *untrusted.get("known_facts_external", [])]
    if offer_note:
        facts.append({"text": offer_note, "source": "пользователь"})
    unknown = ["цены", "УТП", "аудитория", "конкуренты", "лучшие посты"]
    placeholder = Segment(name=_mark(NOT_FOUND))
    return Passport(
        offer=_mark(offer_note or NOT_FOUND),
        audience=_mark(NOT_FOUND),
        usp=_mark(NOT_FOUND),
        segments=(placeholder, placeholder),
        recommended_objective=Objective(q.get("objective") or Objective.DIRECT_MESSAGES),
        budget_rationale=_mark(NOT_FOUND),
        cpa_rationale=_mark(NOT_FOUND),
        risks=(_mark("Анализ не проводился: паспорт составлен только из анкеты"),),
        voice=Voice(language="ru", address="вы", style=_mark("нейтральный")),
        facts=facts,
        not_found=unknown,
    )


def media_plan(task: dict[str, Any]) -> MediaPlanProposal:
    q = task.get("questionnaire", {})
    objective = Objective(q.get("objective") or task.get("passport_objective") or Objective.DIRECT_MESSAGES)
    interests = [i["name"] for i in task.get("interests_found", [])][:3]
    keywords = tuple(interests or [NOT_FOUND])
    geo = (q.get("geo"),) if q.get("geo") else ()
    with_retargeting = bool(task.get("retargeting_available")) and task.get("max_groups", 0) >= MIN_GROUPS_WITH_RETARGETING
    shares = SHARES_WITH_RETARGETING if with_retargeting else SHARES_TWO
    groups = [
        GroupProposal(name=_mark("Широкая"), hypothesis=Hypothesis.BROAD, budget_share=shares[0],
                      locations=geo, rationale=_mark("шаблон")),
        GroupProposal(name=_mark("Интересы"), hypothesis=Hypothesis.INTERESTS, budget_share=shares[1],
                      interest_keywords=keywords, locations=geo, rationale=_mark("шаблон")),
    ]
    if with_retargeting:
        groups.append(GroupProposal(name=_mark("Ретаргетинг"), hypothesis=Hypothesis.RETARGETING, budget_share=shares[2],
                                    locations=geo, rationale=_mark("шаблон")))
    return MediaPlanProposal(
        objective=objective,
        objective_rationale=_mark("цель из анкеты или паспорта"),
        groups=tuple(groups),
        notes=_mark("План по шаблону"),
    )


def ad_copy(task: dict[str, Any]) -> AdCopySet:
    name = task["company_name"]
    untrusted = task.get("_untrusted", {})
    facts = [f["text"] for f in [*task.get("user_facts", []), *untrusted.get("facts_external", [])]]
    cta = CTA_BY_OBJECTIVE.get(task["objective"], "LEARN_MORE")
    headline = name[:HEADLINE_MAX]
    bodies = [f"{name}: {facts[0]}" if facts else f"Расскажем о {name}"]
    bodies.append(f"{name}: {facts[1]}" if len(facts) > 1 else f"{name} — подробности в профиле")
    variants = [
        AdVariant(kind="new_creative", primary_text=_mark(body), headline=headline, cta=cta)
        for body in bodies
    ]
    creatives = untrusted.get("creatives") or []
    if creatives:
        variants[0] = AdVariant(kind="existing_post", creative_id=creatives[0]["id"], cta=cta)
    return AdCopySet(variants=tuple(variants))


def _letters_caps_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(c.isupper() for c in letters) / len(letters) if letters else 0.0


_FORBIDDEN_STEMS = ("оруж", "вейп", "табак", "наркот", "сигарет")
CHECKED_RULES = frozenset({2, 5, 8})


def _review(ad: dict[str, Any]) -> AdVerdict:
    text, headline = ad.get("text", ""), ad.get("headline", "")
    violations = []
    if any(stem in f"{text} {headline}".lower() for stem in _FORBIDDEN_STEMS):
        violations.append(Violation(rule=2, description="Запрещённый контент", fix="Уберите товар из рекламы"))
    if _letters_caps_share(text) > CAPS_SHARE_MAX or "!!!" in text:
        violations.append(Violation(rule=5, description="Капс или «!!!»", fix="Пишите обычным регистром"))
    if len(text) > TEXT_MAX or len(headline) > HEADLINE_MAX:
        violations.append(Violation(rule=8, description="Слишком длинный текст", fix="Сократите"))
    bad = {v.rule for v in violations}
    checks = tuple(
        ChecklistItem(rule=n, ok=(n not in bad) if n in CHECKED_RULES else None,
                      note="" if n in CHECKED_RULES else _mark("не проверено"))
        for n in range(1, 10)
    )
    return AdVerdict(ad_index=ad["index"], checks=checks, violations=tuple(violations))


def compliance(task: dict[str, Any]) -> ComplianceVerdict:
    hint = task.get("category_hint")
    return ComplianceVerdict(
        ads=tuple(_review(ad) for ad in task.get("_untrusted", {}).get("ads", [])),
        category=SpecialCategory(hint) if hint else None,
        category_rationale=_mark("категория из подсказки"),
    )


def optimizer(task: dict[str, Any]) -> OptimizerOutput:
    return OptimizerOutput(
        explanations=tuple(
            Explanation(decision_index=d["index"], text=_mark(d["reason"])) for d in task.get("decisions", [])
        )
    )


def report(task: dict[str, Any]) -> Report:
    period = "week" if task.get("period") == "week" else "day"
    changes = task.get("changes", [])
    return Report(
        period=period,
        summary=_mark(f"Отчёт за {'неделю' if period == 'week' else 'день'}: {task['company_name']}"),
        sections=(
            ReportSection(
                title="Изменения",
                text=_mark("; ".join(changes) if changes else "изменений не было"),
            ),
        ),
        insights=(),
        recommendations=(),
    )


# --- разбор намерений ------------------------------------------------------

_HANDLE_RE = re.compile(r"@([\w.]+)")
_SITE_RE = re.compile(r"https?://\S+")


def _match(text: str, *stems: str) -> bool:
    return any(re.search(rf"\b{stem}", text) for stem in stems)


_CURRENCY = (
    r"долл\w*|usd|eur|евро|руб\w*|сум\w*|сом\w*|тенге|тг|kzt|uzs|uah|грн|гривн\w*|[$€£₽₸]"
)
_AMOUNT_RE = re.compile(
    rf"(?:[$€£₽₸]\s*)?\d[\d\s.,]*(?:\s*(?:тыс\w*|млн|млрд|[кkм](?!\w)))?(?:\s*(?:{_CURRENCY}))?",
    re.IGNORECASE,
)
_AMOUNT_ANCHOR = re.compile(r"лимит|бюджет", re.IGNORECASE)
AMOUNT_TEXT_MAX = 60


def _amount_text(raw: str) -> str | None:
    """Сырой фрагмент суммы после слов «лимит»/«бюджет»; число и валюту разбирает код сервиса."""
    anchor = _AMOUNT_ANCHOR.search(raw)
    match = _AMOUNT_RE.search(raw, anchor.end() if anchor else 0)
    if match is None:
        return None
    fragment = match.group(0).strip(" ,.")
    return fragment if len(fragment) <= AMOUNT_TEXT_MAX else None


def _add_company(raw: str) -> Intent:
    handle = _HANDLE_RE.search(raw)
    site = _SITE_RE.search(raw)
    rest = re.split(r"компани[юи]", raw, maxsplit=1, flags=re.IGNORECASE)[-1]
    rest = re.split(r"бюджет", rest, maxsplit=1, flags=re.IGNORECASE)[0]
    name = _SITE_RE.sub("", _HANDLE_RE.sub("", rest)).strip(" ,.:;")
    return Intent(
        kind=IntentKind.ADD_COMPANY,
        company=name or (handle.group(1) if handle else None),
        instagram_handle=handle.group(1) if handle else None,
        website=site.group(0) if site else None,
        amount_text=_amount_text(raw) if re.search(r"бюджет", raw, re.IGNORECASE) else None,
        comment=DEMO_MARK,
    )


def intent(task: dict[str, Any]) -> Intent:
    raw = str(task.get("user_text", ""))
    low = raw.lower()
    if _match(low, "добав\\w* факт", "факт:"):
        return Intent(kind=IntentKind.ADD_FACT, text=re.split(r"факт[:\s]*", raw, maxsplit=1, flags=re.IGNORECASE)[-1].strip(), comment=DEMO_MARK)
    if _match(low, "добав\\w* компани"):
        return _add_company(raw)
    if _match(low, "стоп"):
        return Intent(kind=IntentKind.HALT, comment=DEMO_MARK)
    if _match(low, "возобнов", "продолж", "запусти снова"):
        return Intent(kind=IntentKind.RESUME, comment=DEMO_MARK)
    if _match(low, "пауз", "останов"):
        return Intent(kind=IntentKind.PAUSE, comment=DEMO_MARK)
    if _match(low, "отч[её]т", "статистик"):
        period = "week" if _match(low, "недел") else "day"
        return Intent(kind=IntentKind.REPORT, report_period=period, comment=DEMO_MARK)
    if _match(low, "план"):
        return Intent(kind=IntentKind.EDIT_PLAN, text=raw.strip(), comment=DEMO_MARK)
    if _match(low, "лимит", "бюджет"):
        amount = _amount_text(raw)
        if amount is None:
            return Intent(comment=_mark("Не понял сумму лимита"))
        return Intent(kind=IntentKind.SET_LIMIT, amount_text=amount, comment=DEMO_MARK)
    if _match(low, "настройк"):
        return Intent(kind=IntentKind.SETTING, text=raw.strip(), comment=DEMO_MARK)
    return Intent(comment=_mark("Не понял сообщение"))


HANDLERS: dict[type[BaseModel], Handler] = {
    Passport: passport,
    MediaPlanProposal: media_plan,
    AdCopySet: ad_copy,
    ComplianceVerdict: compliance,
    OptimizerOutput: optimizer,
    Report: report,
    Intent: intent,
}
