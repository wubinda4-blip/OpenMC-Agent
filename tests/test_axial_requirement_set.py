"""Phase 8A Step 6C — AxialGeometryRequirementSet + preflight tests."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.axial_requirements import (
    AxialGeometryRequirementSet,
    AxialOverlayContract,
    AxialRegionContract,
    AXIAL_REQUIREMENT_SCHEMA_VERSION,
    extract_axial_geometry_requirements,
)
from openmc_agent.plan_investigation.axial_preflight import (
    AXIAL_HOMOGENIZATION_METHOD_MISSING,
    AXIAL_MIXTURE_FRACTION_MISSING,
    AXIAL_REGION_OVERLAP,
    AXIAL_SOURCE_CRITICAL_UNRESOLVED,
    AXIAL_SOURCE_EXTENT_MISSING,
    AxialPreflightExecutionResult,
    run_evidence_qualified_axial_preflight,
)


class _FakeFacts:
    def __init__(
        self,
        *,
        active_fuel_region_cm: tuple | None = None,
        expected_spacer_grid_count: int | None = None,
    ) -> None:
        self.active_fuel_region_cm = active_fuel_region_cm
        self.expected_spacer_grid_count = expected_spacer_grid_count


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


def test_compiler_extracts_axial_domain() -> None:
    facts = _FakeFacts(active_fuel_region_cm=(-200.0, 200.0))
    rs = extract_axial_geometry_requirements(accepted_facts=facts)
    assert rs.axial_domain == (-200.0, 200.0)


def test_compiler_flags_missing_axial_domain() -> None:
    """Missing axial domain is not invented."""

    facts = _FakeFacts(active_fuel_region_cm=None)
    rs = extract_axial_geometry_requirements(accepted_facts=facts)
    assert rs.axial_domain is None


def test_compiler_declares_spacer_grid_overlay_contracts() -> None:
    """Spacer grid count → that many overlay contracts are declared.

    Compiler declares the NEED but does NOT invent z extents or
    mixture fractions.
    """

    facts = _FakeFacts(expected_spacer_grid_count=3)
    rs = extract_axial_geometry_requirements(accepted_facts=facts)
    assert len(rs.overlay_requirements) == 3
    for overlay in rs.overlay_requirements:
        assert overlay.overlay_kind == "spacer_grid"
        # Method + fractions NOT invented.
        assert overlay.homogenization_method == ""
        assert overlay.mixture_fractions == {}
        # Human confirmation required (cannot auto-fill).
        assert overlay.requires_human_confirmation is True
    # Unresolved list records the missing method.
    assert any("homogenization_method" in u for u in rs.unresolved_requirements)


def test_requirement_set_has_stable_hash() -> None:
    facts = _FakeFacts(active_fuel_region_cm=(-200.0, 200.0))
    rs1 = extract_axial_geometry_requirements(accepted_facts=facts, ledger_hash="lh")
    rs2 = extract_axial_geometry_requirements(accepted_facts=facts, ledger_hash="lh")
    assert rs1.requirement_set_hash == rs2.requirement_set_hash


def test_schema_version() -> None:
    assert AXIAL_REQUIREMENT_SCHEMA_VERSION == "1.0"


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def test_preflight_blocks_on_missing_axial_domain() -> None:
    facts = _FakeFacts(active_fuel_region_cm=None)
    rs = extract_axial_geometry_requirements(accepted_facts=facts)
    report = run_evidence_qualified_axial_preflight(requirement_set=rs)
    codes = [f.code for f in report.findings]
    assert AXIAL_SOURCE_EXTENT_MISSING in codes


def test_preflight_blocks_on_missing_homogenization_method() -> None:
    """Spacer grid without homogenization method → block."""

    facts = _FakeFacts(expected_spacer_grid_count=2)
    rs = extract_axial_geometry_requirements(accepted_facts=facts)
    report = run_evidence_qualified_axial_preflight(requirement_set=rs)
    codes = [f.code for f in report.findings]
    assert AXIAL_HOMOGENIZATION_METHOD_MISSING in codes
    assert AXIAL_MIXTURE_FRACTION_MISSING in codes


def test_preflight_blocks_on_overlapping_regions() -> None:
    """Two regions that overlap → blocking finding."""

    rs = AxialGeometryRequirementSet(
        axial_domain=(0.0, 100.0),
        axial_regions=(
            AxialRegionContract(region_kind="fuel", z_min_cm=10.0, z_max_cm=50.0),
            AxialRegionContract(region_kind="plenum", z_min_cm=40.0, z_max_cm=80.0),  # overlaps
        ),
    )
    report = run_evidence_qualified_axial_preflight(requirement_set=rs)
    codes = [f.code for f in report.findings]
    assert AXIAL_REGION_OVERLAP in codes


def test_preflight_blocks_on_unresolved_source_critical() -> None:
    rs = AxialGeometryRequirementSet(
        axial_domain=(0.0, 100.0),
        unresolved_requirements=("axial_region:gas_gap:z_max_cm",),
    )
    report = run_evidence_qualified_axial_preflight(requirement_set=rs)
    codes = [f.code for f in report.findings]
    assert AXIAL_SOURCE_CRITICAL_UNRESOLVED in codes


def test_preflight_passes_when_no_blocking_findings() -> None:
    rs = AxialGeometryRequirementSet(
        axial_domain=(0.0, 100.0),
        axial_regions=(
            AxialRegionContract(region_kind="fuel", z_min_cm=10.0, z_max_cm=50.0),
            AxialRegionContract(region_kind="plenum", z_min_cm=50.0, z_max_cm=80.0),
        ),
    )
    report = run_evidence_qualified_axial_preflight(requirement_set=rs)
    assert report.passed


def test_execution_result_no_requirement_set_is_not_blocking() -> None:
    result = AxialPreflightExecutionResult(
        executed=False, requirement_set_present=False,
    )
    assert result.has_blocking_deterministic_finding is False


def test_execution_result_crash_is_blocking() -> None:
    result = AxialPreflightExecutionResult(
        executed=False,
        requirement_set_present=True,
        execution_error="ValueError",
        failure_code="axial.evidence_preflight_exception",
    )
    assert result.has_blocking_deterministic_finding is True


# ---------------------------------------------------------------------------
# Reactor-neutrality
# ---------------------------------------------------------------------------


def test_compiler_no_reactor_specific_branches() -> None:
    from pathlib import Path
    import openmc_agent.plan_investigation.axial_requirements as mod
    src = Path(mod.__file__).read_text()
    for forbidden in ("vera3", "vera4", "pwr", "bwr", "vver", "htgr", "sfr", "candu"):
        assert forbidden not in src.lower(), (
            f"reactor-specific term {forbidden!r} found"
        )
