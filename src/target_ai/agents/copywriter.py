"""Копирайтер: варианты объявлений из фактов и голоса компании."""

from target_ai.llm import LLM

from ._runner import dump, message, untrusted_json
from .inputs import CopyInput
from .prompts import COPYWRITER
from .schemas import AdCopySet


async def write_copy(llm: LLM, data: CopyInput) -> AdCopySet:
    payload = {
        "company_name": data.company_name,
        "objective": data.objective.value,
        "hypothesis": data.hypothesis,
        "user_facts": dump(data.user_facts),
        "feedback": data.feedback,
    }
    blocks = [
        untrusted_json("voice", data.voice),
        untrusted_json("creatives", data.creatives),
        untrusted_json("facts_external", data.external_facts),
        untrusted_json("segment", data.segment),
    ]
    return await llm.structured("cheap", COPYWRITER, message(payload, *blocks), AdCopySet)
