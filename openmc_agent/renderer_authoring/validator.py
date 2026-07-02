"""Static / AST validation stub for agent-authored renderers.

Any auto-generated renderer MUST pass these checks before it is allowed to run.
The checks are documented here as the policy; enforcement is a TODO.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from openmc_agent.renderer_authoring.planner import SafetyConstraints


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    violations: tuple[str, ...] = ()
    forbidden_modules_seen: tuple[str, ...] = ()
    forbidden_calls_seen: tuple[str, ...] = ()


def validate_renderer_source(
    source_code: str,
    *,
    constraints: SafetyConstraints = SafetyConstraints(),
) -> ValidationResult:
    """Reject empty or policy-violating renderer source.

    This deliberately only inspects syntax and import/call names. It never
    executes the candidate code.
    """
    if not source_code.strip():
        return ValidationResult(is_valid=False, violations=("renderer source is empty",))

    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        return ValidationResult(is_valid=False, violations=(f"syntax error: {exc}",))

    violations: list[str] = []
    modules_seen: list[str] = []
    calls_seen: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in constraints.forbidden_modules:
                    modules_seen.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in constraints.forbidden_modules:
                modules_seen.append(node.module)
        elif isinstance(node, ast.Call):
            call_name = _dotted_name(node.func)
            if call_name and any(
                call_name == forbidden or call_name.startswith(forbidden + ".")
                for forbidden in constraints.forbidden_calls
            ):
                calls_seen.append(call_name)

    if modules_seen:
        violations.append(f"forbidden modules imported: {sorted(set(modules_seen))}")
    if calls_seen:
        violations.append(f"forbidden calls used: {sorted(set(calls_seen))}")

    return ValidationResult(
        is_valid=not violations,
        violations=tuple(violations),
        forbidden_modules_seen=tuple(sorted(set(modules_seen))),
        forbidden_calls_seen=tuple(sorted(set(calls_seen))),
    )


def _dotted_name(node: ast.expr) -> str:
    parts: list[str] = []
    current: ast.expr | None = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return ""
