"""Проверка объявлений плана: схема, медиатека, Контролёр (код) и факты."""

from __future__ import annotations

import re

from target_ai.agents.schemas import ComplianceVerdict

from .compliance import controller_check
from .facts import check_facts
from .plan_types import AD_KINDS, AdInput, PlanContext, ValidatedAd
from .rules import MAX_ADS_PER_GROUP

_INJECTION = re.compile(
    r"игнорир\w*\s+(?:все\s+|любые\s+|предыдущие\s+)?(?:лимит|правил|инструкц)\w*|"
    r"ignore\s+(?:all\s+|previous\s+)?(?:limits?|rules|instructions)|"
    r"обойди\s+лимит|без\s+лимит",
    re.IGNORECASE,
)


def _problem(ad: AdInput, ctx: PlanContext) -> tuple[str | None, bool]:
    """Причина отказа (или None) и признак «ограниченный контент → 18+»."""
    if ad.kind not in AD_KINDS:
        return f"вид «{ad.kind}» не поддерживается", False
    if ad.creative_id not in ctx.creative_ids:
        return f"креатива {ad.creative_id or '(пусто)'} нет в медиатеке компании", False
    if ad.kind == "new_creative" and not ad.text.strip():
        return "у нового креатива нет текста", False
    if _INJECTION.search(ad.text) or _INJECTION.search(ad.headline):
        return "в тексте есть попытка обойти правила", False
    report = controller_check(ad.text, headline=ad.headline, language=ctx.language)
    if report.findings:
        return " ".join(f.message_ru for f in report.findings), report.requires_adult
    if ad.kind == "new_creative":
        violations = check_facts(f"{ad.text} {ad.headline}", ctx.facts)
        if violations:
            return "нет опоры в фактах: " + " ".join(violations), report.requires_adult
    return None, report.requires_adult


def _verdict_problem(verdict: ComplianceVerdict | None, index: int, ctx: PlanContext) -> str | None:
    """Вне демо проходит только status == ok; нет вердикта по объявлению — то же, что needs_review."""
    if verdict is None:
        return None if ctx.demo else "Контролёр не опрошен (нет вердикта) — вне демо объявление не проходит"
    found = next((v for v in verdict.ads if v.ad_index == index), None)
    if found is None:
        return None if ctx.demo else "Контролёр не вынес вердикт по объявлению"
    if found.status == "violation":
        details = " ".join(v.description for v in found.violations) or "нарушение чек-листа"
        return f"Контролёр отклонил: {details}"
    if found.status == "needs_review" and not ctx.demo:
        return "Контролёр проверил не все пункты чек-листа (needs_review)"
    return None


def validate_ads(
    ads: tuple[AdInput, ...], ctx: PlanContext, label: str, notes: list[str],
    verdict: ComplianceVerdict | None = None,
) -> tuple[tuple[ValidatedAd, ...], bool]:
    """До трёх проверенных объявлений на группу и признак «нужно 18+»."""
    kept: list[ValidatedAd] = []
    adult = False
    seen: set[tuple[str, str]] = set()
    for index, ad in enumerate(ads):
        reason, ad_adult = _problem(ad, ctx)
        reason = reason or _verdict_problem(verdict, index, ctx)
        key = (ad.kind, ad.creative_id)
        if reason is None and key in seen:
            reason = "повтор: этот креатив уже есть в группе"
        if reason is not None:
            notes.append(f"{label}: объявление с креативом {ad.creative_id or '(пусто)'} убрано — {reason}.")
            continue
        seen.add(key)
        adult = adult or ad_adult
        kept.append(ValidatedAd(ad.kind, ad.text, ad.creative_id, ad.headline))
    if len(kept) > MAX_ADS_PER_GROUP:
        notes.append(f"{label}: оставлено {MAX_ADS_PER_GROUP} объявления, лишние убраны.")
        kept = kept[:MAX_ADS_PER_GROUP]
    return tuple(kept), adult
