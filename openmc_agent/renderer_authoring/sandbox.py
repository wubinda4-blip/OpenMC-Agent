"""Sandbox execution stub for agent-authored renderers.

A candidate renderer that passes :mod:`openmc_agent.renderer_authoring.validator`
would still have to be executed inside an isolated, write-limited sandbox, run
unit tests, and prove it can call ``model.export_to_xml()`` before registration.
None of that is wired up yet; this module documents the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openmc_agent.renderer_authoring.planner import SafetyConstraints


@dataclass(frozen=True)
class SandboxResult:
    ran: bool
    unit_tests_passed: bool
    export_to_xml_passed: bool
    output_dir: str = ""
    error: str = ""


def run_in_sandbox(
    source_code: str,
    *,
    sandbox_dir: Path,
    constraints: SafetyConstraints = SafetyConstraints(),
) -> SandboxResult:
    """Refuse to run candidate renderers; sandboxing is not implemented."""
    return SandboxResult(
        ran=False,
        unit_tests_passed=False,
        export_to_xml_passed=False,
        output_dir=str(sandbox_dir),
        error=(
            "Sandbox execution is not implemented. Auto-generated renderers must "
            "not be executed until AST validation, sandboxing, unit tests, and an "
            "export_to_xml check are all in place."
        ),
    )
