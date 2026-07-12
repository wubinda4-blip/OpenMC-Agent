"""Tests for capability blocker classification (P0-D5A Section 2)."""
from __future__ import annotations

import json

from openmc_agent.capability_blockers import (
    classify_capability_blockers,
    is_environment_blocker_code,
    is_structural_blocker_code,
)
from openmc_agent.llm import normalize_capability_report
from openmc_agent.schemas import (
    ComplexMaterialSpec,
    ComplexModelSpec,
    NuclideSpec,
    PlotSpec,
    RenderCapabilityReport,
    SimulationPlan,
    ValidationIssue,
    ValidationReport,
)


def _plan_with_capability(
    capability: RenderCapabilityReport,
    *,
    assumptions: list[str] | None = None,
) -> SimulationPlan:
    model = ComplexModelSpec(
        name="assembly",
        kind="assembly",
        materials=[
            ComplexMaterialSpec(
                id="fuel",
                name="fuel",
                density_unit="g/cm3",
                density_value=10.4,
                composition=[NuclideSpec(name="U235", percent=1.0)],
            )
        ],
    )
    return SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=model,
        capability_report=capability,
        expert_assumptions=assumptions or [],
        plot_specs=[PlotSpec(basis="xy", width_cm=(2.52, 2.52), filename="assembly.png")],
    )


def test_structural_issue_routed_before_material_assumptions() -> None:
    """A structural blocker must be classified as agent-fixable, not masked by
    material assumptions."""
    cap = RenderCapabilityReport(
        renderability="skeleton",
        is_executable=False,
        supported_renderer="none",
        issues=[
            ValidationIssue(
                severity="error",
                code="lattice.universe_ref_missing",
                message="lattice references missing universe",
                route_hint="reflect_plan",
            )
        ],
    )
    plan = _plan_with_capability(
        cap, assumptions=["enrichment is approximate", "material fuel: composition_status=approximate"]
    )
    summary = classify_capability_blockers(plan)
    assert any(i.code == "lattice.universe_ref_missing" for i in summary.structural_agent_fixable)
    assert summary.has_blocking_issue
    # Material assumptions are recorded but are NOT blocking.
    assert len(summary.material_assumptions) == 2
    assert not summary.environment_required


def test_structural_code_helpers() -> None:
    assert is_structural_blocker_code("lattice.universe_ref_missing")
    assert is_structural_blocker_code("lattice_transform.replacement_universe_missing")
    assert is_structural_blocker_code("renderer.axial_loading_materialization_failed")
    assert is_structural_blocker_code("cell.material_ref_missing")
    assert not is_structural_blocker_code("runtime.cross_sections_missing")
    assert is_environment_blocker_code("runtime.cross_sections_missing")
    assert is_environment_blocker_code("runtime.geometry_overlap")


def test_environment_issue_classified_separately() -> None:
    """Environment (runtime) issues route to their own bucket and can still be
    escalated to an expert."""
    cap = RenderCapabilityReport(
        renderability="skeleton",
        is_executable=False,
        supported_renderer="none",
        issues=[
            ValidationIssue(
                severity="error",
                code="runtime.cross_sections_missing",
                message="cross sections not configured",
                requires_human_confirmation=True,
                route_hint="ask_expert",
            )
        ],
    )
    plan = _plan_with_capability(cap)
    summary = classify_capability_blockers(plan)
    assert any(i.code == "runtime.cross_sections_missing" for i in summary.environment_required)
    # runtime.cross_sections_missing is ask_expert in the catalog; it lands in
    # environment (prefix) and is also a human-confirmation candidate, but the
    # environment prefix wins for the primary blocker code.
    assert "runtime.cross_sections_missing" in summary.primary_blocker_codes


def test_runnable_with_nonblocking_assumptions_has_no_blocker() -> None:
    """A runnable model with only approximate material assumptions is NOT blocked."""
    cap = RenderCapabilityReport(
        renderability="runnable",
        is_executable=True,
        supported_renderer="assembly",
    )
    plan = _plan_with_capability(
        cap, assumptions=["enrichment approximate", "boron concentration approximate"]
    )
    summary = classify_capability_blockers(plan)
    assert not summary.has_blocking_issue
    assert not summary.structural_agent_fixable
    assert len(summary.material_assumptions) == 2


def test_real_vera3b_primary_blocker_codes() -> None:
    """The real VERA3B fixture's primary blocker is the axial materialization
    defect, not a material fact gap."""
    raw = json.loads(open("tests/fixtures/regressions/vera3b_pre_grid_repair_plan.json").read())
    normalize_capability_report(raw)
    plan = SimulationPlan.model_validate(raw)
    summary = classify_capability_blockers(plan)
    assert "lattice_transform.replacement_universe_missing" in summary.primary_blocker_codes
    assert "renderer.axial_loading_materialization_failed" in summary.primary_blocker_codes
    assert summary.has_blocking_issue


def test_classification_uses_route_hint_not_keywords() -> None:
    """An issue with route_hint=ask_expert is human-fact regardless of code prefix."""
    cap = RenderCapabilityReport(
        renderability="skeleton",
        is_executable=False,
        supported_renderer="none",
        issues=[
            ValidationIssue(
                severity="error",
                code="lattice.hex.rings_missing",
                message="hex lattice rings not declared",
                route_hint="ask_expert",
            )
        ],
    )
    plan = _plan_with_capability(cap)
    summary = classify_capability_blockers(plan)
    # ask_expert route_hint → human_fact, not structural.
    assert any(i.code == "lattice.hex.rings_missing" for i in summary.human_fact_required)
    assert not any(i.code == "lattice.hex.rings_missing" for i in summary.structural_agent_fixable)
