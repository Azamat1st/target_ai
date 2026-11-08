"""Контролёр: проверка текстов и таргетинга по чек-листу."""

from target_ai.llm import LLM

from ._runner import dump, message, untrusted_json
from .inputs import ControlInput
from .prompts import CONTROLLER
from .schemas import ComplianceVerdict


async def control(llm: LLM, data: ControlInput) -> ComplianceVerdict:
    payload = {
        "targeting": dump(dict(data.targeting)),
        "user_facts": dump(data.user_facts),
        "language": data.language,
        "category_hint": data.category.value if data.category else None,
        "objective": data.objective.value if data.objective else None,
    }
    blocks = [untrusted_json("ads", data.ads), untrusted_json("facts_external", data.external_facts)]
    return await llm.structured("strong", CONTROLLER, message(payload, *blocks), ComplianceVerdict)
