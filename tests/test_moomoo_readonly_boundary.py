"""Architectural guardrail for the project's broker read-only boundary."""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = ("src", "data_provider", "api", "bot", "scripts")

# These are the write-capable order APIs exposed by Moomoo trade contexts. Keep
# this denylist explicit: query APIs (orders, fills, positions and fees) remain
# available, while unlocking, placing, changing and cancelling stay impossible
# in project-owned production Python.
FORBIDDEN_TRADE_ACTIONS = frozenset(
    {
        "unlock_trade",
        "place_order",
        "place_combo_order",
        "modify_order",
        "change_order",
        "cancel_all_order",
    }
)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _constant_string(
    node: ast.AST,
    constants: dict[str, str],
) -> str | None:
    """Resolve simple compile-time strings used as dynamic attribute names."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_string(node.left, constants)
        right = _constant_string(node.right, constants)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            part = _constant_string(value, constants)
            if part is None:
                return None
            parts.append(part)
        return "".join(parts)
    return None


def _collect_constant_strings(tree: ast.AST) -> dict[str, str]:
    """Collect simple string assignments, including references and joins."""

    expressions: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if value is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    expressions[target.id] = value

    constants: dict[str, str] = {}
    unresolved = dict(expressions)
    while unresolved:
        resolved_names: list[str] = []
        for name, expression in unresolved.items():
            value = _constant_string(expression, constants)
            if value is not None:
                constants[name] = value
                resolved_names.append(name)
        if not resolved_names:
            break
        for name in resolved_names:
            unresolved.pop(name)
    return constants


def _find_forbidden_references(source: str, filename: str) -> list[str]:
    """Return all statically visible references to write-capable trade APIs."""

    tree = ast.parse(source, filename=filename)
    constants = _collect_constant_strings(tree)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_TRADE_ACTIONS:
                violations.append(
                    f"{filename}:{node.lineno} references .{node.attr}"
                )
            continue

        if isinstance(node, ast.Name):
            if node.id in FORBIDDEN_TRADE_ACTIONS:
                violations.append(
                    f"{filename}:{node.lineno} references {node.id}"
                )
            continue

        if isinstance(node, ast.ImportFrom):
            for imported in node.names:
                if imported.name in FORBIDDEN_TRADE_ACTIONS:
                    alias = f" as {imported.asname}" if imported.asname else ""
                    violations.append(
                        f"{filename}:{node.lineno} imports "
                        f"{imported.name}{alias}"
                    )
            continue

        if isinstance(node, ast.Import):
            for imported in node.names:
                imported_name = imported.name.rsplit(".", 1)[-1]
                if imported_name in FORBIDDEN_TRADE_ACTIONS:
                    alias = f" as {imported.asname}" if imported.asname else ""
                    violations.append(
                        f"{filename}:{node.lineno} imports "
                        f"{imported.name}{alias}"
                    )
            continue

        if not isinstance(node, ast.Call) or _call_name(node) != "getattr":
            continue
        if len(node.args) < 2:
            continue
        dynamic_name = _constant_string(node.args[1], constants)
        if dynamic_name in FORBIDDEN_TRADE_ACTIONS:
            violations.append(
                f"{filename}:{node.lineno} dynamically references "
                f"{dynamic_name} via getattr"
            )

    return violations


def test_guard_detects_write_api_reference_bypasses() -> None:
    """Exercise the scanner against common direct and dynamic bypasses."""

    bypasses = {
        "attribute_without_call": "action = context.unlock_trade",
        "direct_name": "place_order(context)",
        "aliased_import": (
            "from moomoo import place_combo_order as submit\nsubmit()"
        ),
        "dotted_import": "import moomoo.modify_order as adjust",
        "literal_getattr": 'getattr(context, "cancel_all_order")()',
        "constant_getattr": (
            'PREFIX = "change_"\nACTION = PREFIX + "order"\n'
            "getattr(context, ACTION)()"
        ),
    }

    for case, source in bypasses.items():
        assert _find_forbidden_references(source, case), (
            f"read-only guard missed bypass case: {case}"
        )


def test_guard_allows_read_only_trade_queries() -> None:
    source = """
orders = context.history_order_list_query()
fills = context.history_order_fill_list_query()
fees = context.order_fee_query()
positions = getattr(context, "position_list_query")()
"""

    assert _find_forbidden_references(source, "readonly_queries.py") == []


def test_production_code_has_no_moomoo_trade_action_references() -> None:
    """Fail closed if production code gains any trading-action reference."""

    violations: list[str] = []
    for root_name in PRODUCTION_ROOTS:
        root = PROJECT_ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(PROJECT_ROOT)
            violations.extend(
                _find_forbidden_references(
                    path.read_text(encoding="utf-8"),
                    str(relative),
                )
            )

    assert not violations, (
        "Moomoo integration is permanently read-only; remove write-capable "
        "trade API references:\n" + "\n".join(violations)
    )
