"""Evaluation-only VERA3B campaign acceptance adapter.

Bridges the test-only ``tests/helpers/vera3_acceptance.py`` helpers into
the evaluation pipeline. Provides a callback that can be passed to
``run_real_campaign`` without importing test code into production.

The acceptance check covers three levels:
  A. Plan-level (pin map, lattice, axial layers, materials)
  B. Rendered XML-level (point probes)
  C. Runtime-level (geometry debug, smoke)

All VERA3-specific constants come from ``tests/fixtures/vera3_reference.json``,
never from production prompts or hardcoded rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


_ACCEPTANCE_CONTRACT_VERSION = "2.0.0"


class FullAcceptanceLoadError(RuntimeError):
    """Raised when full VERA3B acceptance cannot be loaded."""


def _ensure_test_helpers_importable() -> None:
    """Ensure tests.helpers.vera3_acceptance is importable.

    Uses the repo root (computed from this file's location) rather than a
    fragile relative ``Path("tests/helpers")`` lookup.
    """
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def make_vera3b_acceptance_callback(
    xml_dir: Path | None = None,
) -> Any:
    """Create a VERA3B acceptance callback for use in real campaigns.

    Returns a ``(plan) -> (passed, issue_codes)`` callable.

    Raises :class:`FullAcceptanceLoadError` if the test helpers cannot be
    imported (instead of silently falling back to a basic check).
    """
    _ensure_test_helpers_importable()

    try:
        from tests.helpers.vera3_acceptance import (  # noqa: F401
            validate_vera3_plan_structure,
            load_vera3_reference,
        )
    except ImportError as exc:
        raise FullAcceptanceLoadError(
            f"Cannot import tests.helpers.vera3_acceptance: {exc}. "
            "Full acceptance requires the test infrastructure to be available."
        ) from exc

    def _callback(plan: Any) -> tuple[bool, list[str]]:
        if plan is None:
            return False, ["no_plan"]

        from openmc_agent.schemas import SimulationPlan

        if isinstance(plan, dict):
            plan = SimulationPlan.model_validate(plan)

        reference = load_vera3_reference()
        issues = validate_vera3_plan_structure(plan, reference, variant="3B")
        error_codes = [i.code for i in issues if i.severity == "error"]

        if xml_dir and xml_dir.exists():
            from tests.helpers.vera3_acceptance import (
                validate_rendered_vera3_geometry,
                load_vera3_geometry_contract,
            )
            contract = load_vera3_geometry_contract()
            rendered_issues = validate_rendered_vera3_geometry(
                xml_dir, variant="3B", contract=contract,
            )
            error_codes.extend(
                i.code for i in rendered_issues if i.severity == "error"
            )

        return (len(error_codes) == 0), error_codes

    return _callback


def evaluate_vera3_acceptance(
    plan: Any,
    xml_dir: Path | None = None,
) -> dict[str, Any]:
    """Full VERA3B acceptance evaluation returning structured output."""
    _ensure_test_helpers_importable()

    from tests.helpers.vera3_acceptance import (
        validate_vera3_plan_structure,
        load_vera3_reference,
    )
    from openmc_agent.schemas import SimulationPlan

    result: dict[str, Any] = {
        "contract_version": _ACCEPTANCE_CONTRACT_VERSION,
        "variant": "3B",
        "plan_acceptance": {"passed": False, "issues": []},
        "rendered_acceptance": {"passed": None, "issues": []},
        "runtime_acceptance": {"passed": None},
        "overall_passed": False,
    }

    if plan is None:
        result["plan_acceptance"]["issues"] = ["no_plan"]
        return result

    if isinstance(plan, dict):
        try:
            plan = SimulationPlan.model_validate(plan)
        except Exception as exc:
            result["plan_acceptance"]["issues"] = [f"plan_validation_error: {exc}"]
            return result

    reference = load_vera3_reference()
    issues = validate_vera3_plan_structure(plan, reference, variant="3B")
    error_codes = [{"code": i.code, "severity": i.severity, "message": i.message} for i in issues]
    result["plan_acceptance"]["issues"] = error_codes
    result["plan_acceptance"]["passed"] = all(i["severity"] != "error" for i in error_codes)

    if xml_dir and xml_dir.exists():
        from tests.helpers.vera3_acceptance import (
            validate_rendered_vera3_geometry,
            load_vera3_geometry_contract,
        )
        contract = load_vera3_geometry_contract()
        try:
            rendered_issues = validate_rendered_vera3_geometry(
                xml_dir, variant="3B", contract=contract,
            )
            rendered_codes = [{"code": i.code, "severity": i.severity, "message": i.message} for i in rendered_issues]
            result["rendered_acceptance"]["issues"] = rendered_codes
            result["rendered_acceptance"]["passed"] = all(i["severity"] != "error" for i in rendered_codes)
        except Exception as exc:
            result["rendered_acceptance"]["issues"] = [f"rendered_check_error: {exc}"]
            result["rendered_acceptance"]["passed"] = False

    result["overall_passed"] = (
        result["plan_acceptance"]["passed"]
        and (result["rendered_acceptance"]["passed"] is not False)
    )

    return result
