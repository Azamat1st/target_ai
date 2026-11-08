import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src" / "target_ai"
MUTATORS = {"create_campaign", "create_adset", "create_creative", "create_ad", "create_audience",
            "upload_image", "upload_video", "set_status", "set_daily_budget", "set_spend_cap",
            "create_leadgen_form"}
ALLOWED = {"executor", "meta", "fakemeta"}  # fakemeta реализует API, а не вызывает шлюз


def _is_gateway_call(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute):
        return False
    name = node.func.attr
    if name in MUTATORS:
        return True
    return name == "delete"  # у любого получателя: имя не должно прятать шлюз за переменной


def _is_string_lookup(node: ast.Call) -> bool:
    """`getattr(obj, "set_status")` и подобное: обход ограждения по строковому имени."""
    return (isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant) and node.args[1].value in MUTATORS | {"delete"})


def test_only_executor_calls_mutating_gateway_methods():
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.relative_to(SRC).parts[0] in ALLOWED:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and (_is_gateway_call(node) or _is_string_lookup(node)):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == []


def test_fence_catches_delete_on_any_receiver_and_string_lookups():
    calls = [ast.parse(code).body[0].value for code in (
        "x.delete('1')", "self.helper.set_status('1', 'ACTIVE')", "getattr(g, 'set_spend_cap')")]
    assert all(_is_gateway_call(c) or _is_string_lookup(c) for c in calls)
    assert not _is_string_lookup(ast.parse("getattr(g, name)").body[0].value)
