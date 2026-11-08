"""Ключи настроек в базе записаны один раз — в config/runtime.py; остальные модули импортируют их оттуда."""

import ast
from pathlib import Path

import target_ai
from target_ai.config import runtime

SRC = Path(target_ai.__file__).parent
RUNTIME_FILE = SRC / "config" / "runtime.py"
# Имена, у которых нет другого смысла в коде: любое такое строковое значение — повторный литерал ключа.
UNAMBIGUOUS = ("optimizer_rules", "alert_thresholds", "report_time", "report_tz", "install_id", "overspend", "month:{}")
# Эти слова встречаются и в других смыслах (уровень в Meta, код отказа); ключом они считаются только в вызове settings.
AMBIGUOUS = ("level", "halted")


def _literals(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value, node


def test_unambiguous_setting_keys_are_literals_only_in_runtime_module():
    found = [(path.relative_to(SRC).as_posix(), value) for path in SRC.rglob("*.py") if path != RUNTIME_FILE
             for value, _ in _literals(path) if value in UNAMBIGUOUS]

    assert found == []


def _is_settings_access(expr: ast.AST) -> bool:
    return ast.unparse(expr).endswith((".settings.get", ".settings.set"))


def _settings_call_args(node: ast.Call):
    """Аргументы вызова `settings.get/set(...)` и `to_thread(settings.get, ...)` / `io(settings.get, ...)`."""
    if _is_settings_access(node.func):
        return node.args
    if node.args and _is_settings_access(node.args[0]):
        return node.args[1:]
    return ()


def test_level_and_halted_keys_are_never_passed_as_literals_to_settings_calls():
    offenders = []
    for path in SRC.rglob("*.py"):
        if path == RUNTIME_FILE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                offenders += [(path.name, a.value) for a in _settings_call_args(node)
                              if isinstance(a, ast.Constant) and a.value in (*AMBIGUOUS, runtime.DEFAULT_LEVEL)]

    assert offenders == []


def test_default_report_time_and_timezone_are_defined_once():
    assert (runtime.DEFAULT_REPORT_TIME, runtime.DEFAULT_TZ) == ("10:00", "UTC")
    assert runtime.Thresholds().no_impressions_hours == 6
