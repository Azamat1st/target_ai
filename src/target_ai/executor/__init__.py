"""Исполнитель — единственный модуль, меняющий что-либо в Meta (спека §4)."""

from .errors import ApprovalInvalid, ApprovalRequired, ExecutorError, PolicyDenied
from .executor import Executor
from .results import (
    AppliedDecision, ChangeResult, DecisionsReport, EngagementAudience, LaunchResult, PauseReport, RecoveryItem,
)
from .spec import LaunchSpec, parse_spec, plan_data_from_validated

__all__ = [
    "AppliedDecision", "ApprovalInvalid", "ApprovalRequired", "ChangeResult", "DecisionsReport", "EngagementAudience",
    "Executor",
    "ExecutorError", "LaunchResult", "LaunchSpec", "PauseReport", "PolicyDenied", "RecoveryItem",
    "parse_spec", "plan_data_from_validated",
]
