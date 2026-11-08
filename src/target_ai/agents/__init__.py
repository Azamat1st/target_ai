"""Агенты (спека §5): промпты и схемы. Только предлагают; ни базы, ни Meta не видят."""

from .analyst import analyze
from .controller import control
from .copywriter import write_copy
from .inputs import (
    AdForReview,
    AnalystInput,
    ControlInput,
    CopyInput,
    CreativeOption,
    DecisionInfo,
    ExplainInput,
    IntentContext,
    InterestOption,
    Questionnaire,
    ReportInput,
    Screenshot,
    StrategyInput,
)
from .intent import parse_intent
from .optimizer import explain, report
from .schemas import (
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
    OptimizerSuggestion,
    Passport,
    PassportFact,
    Report,
    SpecialCategory,
    Violation,
    Voice,
)
from .strategist import strategize

__all__ = [
    "AdCopySet", "AdForReview", "AdVariant", "AdVerdict", "AnalystInput", "ChecklistItem",
    "ComplianceVerdict", "ControlInput", "CopyInput", "CreativeOption", "DecisionInfo",
    "ExplainInput", "GroupProposal", "Hypothesis", "Intent", "IntentContext", "IntentKind",
    "InterestOption", "MediaPlanProposal", "Objective", "OptimizerOutput", "OptimizerSuggestion",
    "Passport", "PassportFact", "Questionnaire", "Report", "ReportInput", "Screenshot",
    "SpecialCategory", "StrategyInput", "Violation", "Voice", "analyze", "control", "explain",
    "parse_intent", "report", "strategize", "write_copy",
]
