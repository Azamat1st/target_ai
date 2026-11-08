import ast
from datetime import datetime
from pathlib import Path

import pytest

from target_ai.money import Money
from target_ai.policy import CompanySnapshot, coerce_proposal

POLICY_DIR = Path(__file__).resolve().parents[3] / "src" / "target_ai" / "policy"
FORBIDDEN = {"storage", "meta", "executor", "llm", "bot", "services", "fakemeta"}
ALLOWED_AGENT_MODULES = {"target_ai.agents.schemas"}  # pydantic-типы без ввода-вывода


def imported_targets(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_policy_package_imports_no_io_modules():
    for path in POLICY_DIR.glob("*.py"):
        for target in imported_targets(path):
            parts = target.split(".")
            if target.startswith("target_ai.agents"):
                assert target in ALLOWED_AGENT_MODULES, f"{path.name} импортирует {target}"
            if parts[0] == "target_ai":
                assert parts[1] not in FORBIDDEN, f"{path.name} импортирует {target}"
            assert parts[0] not in {"httpx", "sqlite3", "aiogram", "anthropic", "requests"}, path.name


def test_policy_does_no_io_or_wall_clock_reads():
    for path in POLICY_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for banned in ("open(", "print(", "datetime.now(", "time.time(", "float("):
            assert banned not in source, f"{path.name}: {banned}"


def test_snapshot_requires_aware_time():
    usd = Money(1, "USD")
    with pytest.raises(ValueError):
        CompanySnapshot(
            company_id=1, currency="USD", timezone="UTC", now=datetime(2026, 11, 8), mode="confirm",
            level="live", is_demo=False, halted=False, monthly_limit=usd, spent_month=usd,
            min_budget_imp=usd, min_budget_high_freq=usd)


def test_coerce_proposal_survives_garbage_shapes():
    proposal, notes = coerce_proposal({
        "objective": 5, "groups": ["строка", {"hypothesis": "broad", "budget_share": True,
                                             "interest_keywords": "кофе", "radius_km": "5",
                                             "ads": ["x", {"kind": 1, "text": None}]}],
    })
    assert proposal.objective == "" and len(proposal.groups) == 1
    group = proposal.groups[0]
    assert group.budget_share == 0 and group.interest_keywords == () and group.radius_km is None
    assert len(group.ads) == 1 and group.ads[0].kind == ""
    assert len(notes) == 2


def test_coerce_proposal_with_non_list_groups_yields_empty_plan():
    proposal, _ = coerce_proposal({"objective": "reach", "groups": "много"})
    assert proposal.groups == ()
