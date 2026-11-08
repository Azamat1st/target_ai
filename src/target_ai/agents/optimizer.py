"""Оптимизатор: объяснения решений кода, предложения и отчёты."""

from target_ai.llm import LLM

from ._runner import dump, message
from .inputs import ExplainInput, ReportInput
from .prompts import OPTIMIZER, REPORTER
from .schemas import OptimizerOutput, Report


async def explain(llm: LLM, data: ExplainInput) -> OptimizerOutput:
    payload = {
        "company_name": data.company_name,
        "decisions": dump(data.decisions),
        "stats": dump([dict(s) for s in data.stats]),
        "history": list(data.history),
        "suggestions_wanted": data.suggest,
    }
    return await llm.structured("cheap", OPTIMIZER, message(payload), OptimizerOutput)


async def report(llm: LLM, data: ReportInput) -> Report:
    payload = {
        "company_name": data.company_name,
        "period": data.period,
        "stats": dump([dict(s) for s in data.stats]),
        "changes": list(data.changes),
        "history": list(data.history),
        "target_cpa": data.target_cpa,
    }
    tier = "strong" if data.period == "week" else "cheap"
    return await llm.structured(tier, REPORTER, message(payload), Report)
