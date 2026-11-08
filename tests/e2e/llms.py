"""Модели для сквозных тестов: полностью скриптовая и «скрипт, а дальше офлайн»."""

from __future__ import annotations

from decimal import Decimal

from target_ai.agents.schemas import (
    AdCopySet, AdVariant, ComplianceVerdict, GroupProposal, MediaPlanProposal, Objective, OptimizerOutput, Passport,
    Report, Segment, Voice,
)
from target_ai.llm import OfflineLLM, ScriptedLLM
from target_ai.llm.errors import ScriptExhausted


class ScriptThenOffline(ScriptedLLM):
    """Заготовленные ответы идут первыми; когда очередь схемы пуста, отвечает `OfflineLLM`."""

    def __init__(self, responses=()):
        super().__init__(responses)
        self._offline = OfflineLLM()

    async def structured(self, tier, system, user_content, schema, tools=None):
        try:
            return await super().structured(tier, system, user_content, schema, tools)
        except ScriptExhausted:
            return await self._offline.structured(tier, system, user_content, schema, tools)


def good_passport() -> Passport:
    segment = Segment(name="Офисные работники")
    return Passport(
        offer="Кофе и выпечка", audience="Жители Берлина", usp="Свежая обжарка", segments=(segment, segment),
        recommended_objective=Objective.DIRECT_MESSAGES, budget_rationale="по анкете", cpa_rationale="по анкете",
        risks=("сезонность",), voice=Voice(language="ru", address="вы", style="тёплый"), facts=[], not_found=[])


def good_plan() -> MediaPlanProposal:
    def group(name, hypothesis, share, *interests):
        return GroupProposal(name=name, hypothesis=hypothesis, budget_share=Decimal(share), interest_keywords=interests,
                             locations=("DE",), rationale="гипотеза")
    return MediaPlanProposal(
        objective=Objective.DIRECT_MESSAGES, objective_rationale="по анкете",
        groups=(group("Широкая", "broad", "0.4"), group("Кофе", "interests", "0.35", "Кофе"),
                group("Выпечка", "interests", "0.25", "Выпечка")), notes="сценарий")


def good_copy(tag: str = "") -> AdCopySet:
    return AdCopySet(variants=(
        AdVariant(kind="new_creative", primary_text=f"Свежий кофе каждое утро {tag}".strip(), headline="Кофейня", cta="INSTAGRAM_MESSAGE"),
        AdVariant(kind="new_creative", primary_text=f"Выпечка из печи рядом с вами {tag}".strip(), headline="Выпечка", cta="INSTAGRAM_MESSAGE")))


WORDS = ("альфа", "бета", "гамма", "дельта", "эпсилон", "дзета", "эта", "тета", "йота", "каппа", "лямбда", "омега")


def scripted_happy_path() -> ScriptedLLM:
    """Всё, что агенты вернут за цикл «компания → план → запуск → +7 дней», без единого офлайн-ответа."""
    return ScriptedLLM({
        Passport: [good_passport()] * 3,
        MediaPlanProposal: [good_plan()] * 3,
        AdCopySet: [good_copy(WORDS[i]) for i in range(12)],
        ComplianceVerdict: [ComplianceVerdict(ads=())] * 12,
        OptimizerOutput: [OptimizerOutput()] * 8,
        Report: [Report(period="day", summary="Итог дня")] * 8,
    })
