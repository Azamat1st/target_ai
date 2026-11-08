"""Стратег: цель, группы, гипотезы, доли бюджета."""

from target_ai.llm import LLM
from target_ai.money import format_ru

from ._runner import dump, message, untrusted_json
from .inputs import StrategyInput
from .prompts import STRATEGIST
from .schemas import MediaPlanProposal


async def strategize(llm: LLM, data: StrategyInput) -> MediaPlanProposal:
    payload = {
        "passport_objective": data.passport.recommended_objective.value,
        "user_facts": dump(data.user_facts),
        "questionnaire": dump(data.questionnaire),
        "daily_cap": format_ru(data.daily_cap),
        "min_group_budget": format_ru(data.min_group_budget),
        "interests_found": dump(data.interests),
        "history": list(data.history),
        "max_groups": data.max_groups,
        "retargeting_available": data.retargeting_available,
        "lookalike_available": data.lookalike_available,
    }
    blocks = [untrusted_json("passport", data.passport)]
    if data.external_facts:
        blocks.append(untrusted_json("facts_external", data.external_facts))
    return await llm.structured("strong", STRATEGIST, message(payload, *blocks), MediaPlanProposal)
