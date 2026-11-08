"""Копирайтер -> Контролёр + проверка фактов -> один возврат Копирайтеру (спека §3, R23, R66, R67).

Тексты объявлений пишет только Копирайтер; код лишь привязывает присланные медиа к new_creative
и отбрасывает варианты с чужим id поста (строкой в плане).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from target_ai.agents import AdForReview, ControlInput, CopyInput, control, write_copy
from target_ai.agents.schemas import AdCopySet, AdVariant, ComplianceVerdict
from target_ai.policy import check_facts, controller_check

TOO_FEW = "нужно минимум два допустимых варианта"


def _review_ads(copy: AdCopySet, captions: Mapping[str, str]) -> tuple[AdForReview, ...]:
    return tuple(
        AdForReview(index=i, kind=v.kind, text=v.primary_text or captions.get(v.creative_id or "", ""),
                    headline=v.headline, cta=v.cta)
        for i, v in enumerate(copy.variants)
    )


def try_copy(variants: Sequence[AdVariant]) -> AdCopySet | None:
    """Обычный конструктор со схемой (2–3 варианта); не прошло — None."""
    try:
        return AdCopySet(variants=tuple(variants))
    except ValidationError:
        return None


def normalize_variants(copy: AdCopySet, post_ids: set[str], uploaded: list[str], label: str) -> tuple[list[AdVariant], list[str]]:
    """Пост с неизвестным id отбрасывается; присланное медиа привязывается к new_creative (id задаёт код, не текст)."""
    used: set[str] = set()
    kept: list[AdVariant] = []
    notes: list[str] = []
    for variant in copy.variants:
        if variant.kind == "existing_post":
            if variant.creative_id in post_ids:
                kept.append(variant)
            else:
                notes.append(f"«{label}»: вариант с постом {variant.creative_id} убран — такого поста нет среди постов компании.")
            continue
        free = [u for u in uploaded if u not in used]
        creative = variant.creative_id if variant.creative_id in free else (free[0] if free else variant.creative_id)
        used.add(creative or "")
        kept.append(variant.model_copy(update={"creative_id": creative}))
    return kept, notes


def find_problems(copy: AdCopySet, verdict: ComplianceVerdict, fact_texts: list[str], language: str) -> dict[int, list[str]]:
    """Причины, по которым вариант надо переделать: вердикт Контролёра, факты, детерминированные правила."""
    by_index = {a.ad_index: a for a in verdict.ads}
    problems: dict[int, list[str]] = {}
    for i, variant in enumerate(copy.variants):
        reasons: list[str] = []
        ad = by_index.get(i)
        if ad is not None and ad.status == "violation":
            reasons += [v.description for v in ad.violations] or ["нарушение правил Meta"]
        if variant.kind == "new_creative":
            reasons += check_facts(f"{variant.primary_text} {variant.headline}", fact_texts)
            reasons += [f.message_ru for f in controller_check(
                variant.primary_text, headline=variant.headline, language=language).findings]
        if reasons:
            problems[i] = reasons
    return problems


async def write_reviewed_copy(
    llm, base: CopyInput, review: Mapping[str, Any], *, fact_texts: list[str], captions: Mapping[str, str], label: str,
    post_ids: set[str] = frozenset(), uploaded: list[str] = (),
) -> tuple[AdCopySet | None, ComplianceVerdict, list[str]]:
    """Тексты группы + вердикт. Нарушения возвращаются Копирайтеру 1 раз; оставшиеся варианты выбрасываются.
    None — у группы не осталось двух допустимых вариантов."""
    language = base.voice.language

    async def attempt(request: CopyInput):
        raw = await write_copy(llm, request)
        variants, dropped = normalize_variants(raw, set(post_ids), list(uploaded), label)
        copy = try_copy(variants)
        if copy is None:
            return None, ComplianceVerdict(ads=()), {0: [TOO_FEW]}, dropped
        verdict = await control(llm, ControlInput(ads=_review_ads(copy, captions), **review))
        return copy, verdict, find_problems(copy, verdict, fact_texts, language), dropped

    copy, verdict, problems, dropped = await attempt(base)
    if not problems:
        return copy, verdict, dropped
    reasons = "; ".join(sorted({r for rs in problems.values() for r in rs}))
    feedback = f"{base.feedback + '. ' if base.feedback else ''}Исправь: {reasons}"[:900]
    copy, verdict, problems, dropped = await attempt(base.model_copy(update={"feedback": feedback}))
    if copy is None:
        return None, verdict, [*dropped, f"«{label}»: после доработки нет двух допустимых вариантов — группа без объявлений."]
    notes = [*dropped, *(f"«{label}»: объявление {i + 1} убрано после доработки — {'; '.join(rs)}" for i, rs in problems.items())]
    keep = [i for i in range(len(copy.variants)) if i not in problems]
    ads = tuple(a.model_copy(update={"ad_index": keep.index(a.ad_index)}) for a in verdict.ads if a.ad_index in keep)
    return try_copy([copy.variants[i] for i in keep]), verdict.model_copy(update={"ads": ads}), notes
