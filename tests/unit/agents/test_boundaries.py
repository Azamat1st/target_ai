import ast
from pathlib import Path

import target_ai.agents as agents_pkg
from target_ai.agents import GroupProposal, MediaPlanProposal, Passport

FORBIDDEN = {"storage", "meta", "executor"}
MONEY_LIKE = {"amount", "daily_budget", "currency", "limit", "monthly_limit", "mode", "meta_id", "id", "budget"}


def _imported_modules(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            found.add(prefix)
            found.update(f"{prefix}.{alias.name}" for alias in node.names)
    return found


def test_no_agent_module_imports_storage_meta_or_executor():
    root = Path(agents_pkg.__file__).parent
    offenders = []
    for path in root.glob("*.py"):
        for name in _imported_modules(path):
            parts = {p for p in name.replace("target_ai.", "").split(".") if p}
            if parts & FORBIDDEN:
                offenders.append((path.name, name))

    assert offenders == []


def test_plan_proposal_schema_has_no_fields_for_amounts_ids_currency_or_limits():
    plan_fields = set(MediaPlanProposal.model_fields) | set(GroupProposal.model_fields)

    assert plan_fields & MONEY_LIKE == set()


def test_passport_recommendations_are_advice_only_no_limit_fields():
    assert "monthly_limit" not in Passport.model_fields
    assert "recommended_monthly_budget" in Passport.model_fields


def test_extra_fields_from_the_model_are_dropped_not_kept():
    raw = {
        "objective": "reach",
        "daily_budget": 99999999,
        "currency": "USD",
        "groups": [
            {"name": "a", "hypothesis": "broad", "budget_share": "0.5", "meta_id": "123"},
            {"name": "b", "hypothesis": "interests", "budget_share": "0.5"},
        ],
    }

    plan = MediaPlanProposal.model_validate(raw)

    dumped = plan.model_dump()
    assert "daily_budget" not in dumped and "currency" not in dumped
    assert "meta_id" not in dumped["groups"][0]
