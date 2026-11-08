"""Политика денег и правил: чистые функции без ввода-вывода (спека §3)."""

from .actions import (
    Action, Activate, CreateObject, Delete, Launch, NewGroup, Pause, SetDailyBudget,
    SetMonthlyLimit, SetSpendCap, Targeting,
)
from target_ai.agents.schemas import SpecialCategory

from .categories import category_title_ru, classify_special_category, is_forbidden, stricter_category
from .check import action_key, check_action
from .compliance import ControllerFinding, ControllerReport, controller_check
from .facts import check_facts
from .optimizer import OptimizerDecision, apply_decision, check_decisions, decide
from .pace_enforcement import enforce_pace
from .pacing import daily_cap, days_left
from .plan import INSTAGRAM_POSITIONS, validate_plan
from .plan_types import (
    AdInput, GroupInput, InterestInfo, PlanContext, PlanProposalInput, PolicyInvariantError,
    ValidatedAd, ValidatedGroup, ValidatedPlan,
)
from .proposal import coerce_proposal
from .rules import META_DAILY_OVERSPEND, OptimizerRules
from .spend import increases_spend
from .types import (
    Allowed, AdState, CampaignState, CompanySnapshot, Decision, Denied, GroupState, NeedsApproval,
)

__all__ = [
    "OptimizerDecision", "apply_decision", "check_decisions", "decide", "enforce_pace",
    "AdInput", "GroupInput", "INSTAGRAM_POSITIONS", "InterestInfo", "PlanContext", "PlanProposalInput",
    "PolicyInvariantError", "ValidatedAd", "ValidatedGroup", "ValidatedPlan", "coerce_proposal",
    "validate_plan",
    "Action", "Activate", "AdState", "Allowed", "CampaignState", "CompanySnapshot", "CreateObject",
    "Decision", "Delete", "Denied", "GroupState", "Launch", "META_DAILY_OVERSPEND", "NeedsApproval",
    "NewGroup", "OptimizerRules", "Pause", "SetDailyBudget", "SetMonthlyLimit", "SetSpendCap",
    "Targeting", "ControllerFinding", "ControllerReport", "SpecialCategory", "action_key", "check_action", "classify_special_category", "controller_check", "stricter_category", "is_forbidden", "category_title_ru", "check_facts", "daily_cap", "days_left", "increases_spend",
]
