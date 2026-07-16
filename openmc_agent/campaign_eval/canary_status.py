"""Canary status gating for real-LLM planning canaries.

Distinguishes **subcanary** milestones (individual subsystem verified) from
the **full planning canary** (all required stages + assembly succeed).

The full planning canary is only declared when ALL of the following hold:
- all required patch types are valid
- no pending/invalid/blocked required patch
- incremental execution result ok=true
- PlanAssemblyResult.ok=true
- complete SimulationPlan generated
- reference patches used=0
- gold few-shot used=false
- monolithic fallback used=false
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel


# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

FUEL_VARIANT_SUBCANARY_PASSED = "VERA4_REAL_LLM_FUEL_VARIANT_SUBCANARY_PASSED"
PLANNING_CANARY_PASSED = "VERA4_REAL_LLM_PLANNING_CANARY_PASSED"
GRID_DECORATED_FUEL_IDENTITY_VALIDATED = "VERA4_GRID_DECORATED_FUEL_IDENTITY_VALIDATED"
RENDER_CANARY_PASSED = "VERA4_REAL_LLM_RENDER_CANARY_PASSED"
BASE_GEOMETRY_CANARY_PASSED = "VERA4_BASE_GEOMETRY_CANARY_PASSED"
BASE_SMOKE_CANARY_PASSED = "VERA4_BASE_SMOKE_CANARY_PASSED"

# P2 stage readiness flags
AXIAL_OVERLAY_SEMANTIC_CONTRACT_READY = "P2_FULLCORE_AXIAL_OVERLAY_SEMANTIC_CONTRACT_READY"
ISSUE_SCOPED_PATCH_RETRY_READY = "P2_FULLCORE_ISSUE_SCOPED_PATCH_RETRY_READY"
RETRY_DRIFT_GATE_READY = "P2_FULLCORE_RETRY_DRIFT_GATE_READY"

# Forbidden declarations (must NEVER be set by this module)
FORBIDDEN_STATUSES = frozenset({
    "VERA4_11_CASE_CAMPAIGN_PASSED",
    "VERA4_ROD_WORTH_PASSED",
    "VERA4_QUALIFICATION_PASSED",
    "P2_FULLCORE_STAGE_COMPLETE",
})


class CanaryReport(AgentBaseModel):
    """Structured canary status report."""

    # Individual subcanary flags
    fuel_variant_subcanary: bool = False
    planning_canary: bool = False
    grid_decorated_fuel_identity: bool = False
    render_canary: bool = False
    base_geometry_canary: bool = False
    base_smoke_canary: bool = False

    # P2 readiness flags
    axial_overlay_semantic_contract: bool = False
    issue_scoped_patch_retry: bool = False
    retry_drift_gate: bool = False

    # Detailed checks for planning canary
    all_required_patches_valid: bool = False
    no_pending_patches: bool = False
    incremental_ok: bool = False
    assembly_ok: bool = False
    simulation_plan_generated: bool = False
    reference_patches_used: int = 0
    gold_few_shot_used: bool = False
    monolithic_fallback_used: bool = False
    llm_repair_proposer_used: bool = False
    runtime_content_repair_used: bool = False

    # Details
    valid_patch_types: list[str] = Field(default_factory=list)
    invalid_patch_types: list[str] = Field(default_factory=list)
    required_patch_types: list[str] = Field(default_factory=list)
    missing_patch_types: list[str] = Field(default_factory=list)
    detail: str = ""

    def declared_statuses(self) -> list[str]:
        """Return the list of status strings that can be declared."""
        statuses: list[str] = []
        if self.axial_overlay_semantic_contract:
            statuses.append(AXIAL_OVERLAY_SEMANTIC_CONTRACT_READY)
        if self.issue_scoped_patch_retry:
            statuses.append(ISSUE_SCOPED_PATCH_RETRY_READY)
        if self.retry_drift_gate:
            statuses.append(RETRY_DRIFT_GATE_READY)
        if self.fuel_variant_subcanary:
            statuses.append(FUEL_VARIANT_SUBCANARY_PASSED)
        if self.planning_canary:
            statuses.append(PLANNING_CANARY_PASSED)
        if self.grid_decorated_fuel_identity:
            statuses.append(GRID_DECORATED_FUEL_IDENTITY_VALIDATED)
        if self.render_canary:
            statuses.append(RENDER_CANARY_PASSED)
        if self.base_geometry_canary:
            statuses.append(BASE_GEOMETRY_CANARY_PASSED)
        if self.base_smoke_canary:
            statuses.append(BASE_SMOKE_CANARY_PASSED)
        return statuses

    def forbidden_present(self) -> list[str]:
        """Check that no forbidden status is declared."""
        return [s for s in self.declared_statuses() if s in FORBIDDEN_STATUSES]


def evaluate_planning_canary(
    *,
    execution_result: Any | None = None,
    assembly_result: Any | None = None,
    simulation_plan: dict[str, Any] | None = None,
    valid_patch_types: list[str] | None = None,
    invalid_patch_types: list[str] | None = None,
    required_patch_types: list[str] | None = None,
    reference_patches_used: list[str] | None = None,
    gold_few_shot_used: bool = False,
    monolithic_fallback_used: bool = False,
    llm_repair_proposer_used: bool = False,
    runtime_content_repair_used: bool = False,
) -> CanaryReport:
    """Evaluate whether the full planning canary can be declared.

    Parameters
    ----------
    execution_result
        IncrementalExecutionResult (or dict with ``ok`` and ``summary``).
    assembly_result
        PlanAssemblyResult (or dict with ``ok``).
    simulation_plan
        The assembled SimulationPlan dict (None if not assembled).
    valid_patch_types
        List of patch types that are valid.
    required_patch_types
        List of required patch types for this benchmark.
    reference_patches_used
        List of patch types filled from reference fixtures.
    """
    report = CanaryReport()

    # Extract data from execution_result
    if execution_result is not None:
        if hasattr(execution_result, "ok"):
            report.incremental_ok = execution_result.ok
            summary = getattr(execution_result, "summary", {})
        elif isinstance(execution_result, dict):
            report.incremental_ok = execution_result.get("ok", False)
            summary = execution_result.get("summary", {})
        else:
            summary = {}
        if not valid_patch_types and isinstance(summary, dict):
            valid_patch_types = summary.get("valid_patch_types", [])
        if not invalid_patch_types and isinstance(summary, dict):
            invalid_patch_types = summary.get("invalid_patch_types", [])
    else:
        summary = {}

    # Extract from assembly_result
    if assembly_result is not None:
        if hasattr(assembly_result, "ok"):
            report.assembly_ok = assembly_result.ok
        elif isinstance(assembly_result, dict):
            report.assembly_ok = assembly_result.get("ok", False)

    # Patch status
    report.valid_patch_types = list(valid_patch_types or [])
    report.invalid_patch_types = list(invalid_patch_types or [])
    report.required_patch_types = list(required_patch_types or [])
    report.missing_patch_types = [
        pt for pt in report.required_patch_types
        if pt not in report.valid_patch_types
    ]
    report.all_required_patches_valid = (
        len(report.missing_patch_types) == 0
        and len(report.required_patch_types) > 0
    )
    report.no_pending_patches = len(report.invalid_patch_types) == 0

    # Assembly + plan
    report.simulation_plan_generated = simulation_plan is not None

    # Forbidden features
    report.reference_patches_used = len(reference_patches_used or [])
    report.gold_few_shot_used = gold_few_shot_used
    report.monolithic_fallback_used = monolithic_fallback_used
    report.llm_repair_proposer_used = llm_repair_proposer_used
    report.runtime_content_repair_used = runtime_content_repair_used

    # Full planning canary: ALL conditions must hold
    report.planning_canary = all([
        report.all_required_patches_valid,
        report.no_pending_patches,
        report.incremental_ok,
        report.assembly_ok,
        report.simulation_plan_generated,
        report.reference_patches_used == 0,
        not report.gold_few_shot_used,
        not report.monolithic_fallback_used,
        not report.llm_repair_proposer_used,
        not report.runtime_content_repair_used,
    ])

    # Build detail message
    if report.planning_canary:
        report.detail = "All planning canary conditions satisfied."
    else:
        blockers: list[str] = []
        if not report.all_required_patches_valid:
            blockers.append(f"missing patches: {report.missing_patch_types}")
        if not report.no_pending_patches:
            blockers.append(f"invalid patches: {report.invalid_patch_types}")
        if not report.incremental_ok:
            blockers.append("incremental execution failed")
        if not report.assembly_ok:
            blockers.append("assembly failed")
        if not report.simulation_plan_generated:
            blockers.append("no simulation plan")
        if report.reference_patches_used > 0:
            blockers.append(f"reference patches used: {report.reference_patches_used}")
        if report.gold_few_shot_used:
            blockers.append("gold few-shot used")
        if report.monolithic_fallback_used:
            blockers.append("monolithic fallback used")
        report.detail = "Blocked: " + "; ".join(blockers) if blockers else "Unknown blocker"

    return report


def evaluate_fuel_variant_subcanary(
    *,
    valid_patch_types: list[str],
    fuel_variant_requirements: list[dict] | None = None,
    assembly_fuel_binding_summaries: list[dict] | None = None,
) -> CanaryReport:
    """Evaluate whether the fuel variant subcanary can be declared.

    This is declared when the fuel variant source contract is verified:
    - fuel_variant_requirements present in context
    - Each fuel assembly type has a fuel_variant_id
    - Each variant maps to a distinct material
    """
    report = CanaryReport()

    has_requirements = bool(fuel_variant_requirements)
    has_bindings = bool(assembly_fuel_binding_summaries)
    has_materials = "materials" in valid_patch_types
    has_catalog = "assembly_catalog" in valid_patch_types

    report.fuel_variant_subcanary = (
        has_requirements and has_materials and has_catalog and has_bindings
    )
    report.valid_patch_types = list(valid_patch_types)

    if report.fuel_variant_subcanary:
        report.detail = (
            f"Fuel variant contract verified: {len(fuel_variant_requirements or [])} "
            f"variants, {len(assembly_fuel_binding_summaries or [])} bindings."
        )
    else:
        missing = []
        if not has_requirements:
            missing.append("no fuel_variant_requirements")
        if not has_materials:
            missing.append("materials patch missing")
        if not has_catalog:
            missing.append("assembly_catalog patch missing")
        if not has_bindings:
            missing.append("no fuel binding summaries")
        report.detail = "Fuel variant subcanary blocked: " + ", ".join(missing)

    return report
