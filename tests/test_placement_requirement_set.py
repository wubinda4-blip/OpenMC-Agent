"""Phase 8A Step 6C — PlacementRequirementSet + preflight tests."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.placement_requirements import (
    AssemblyTypePlacementRequirement,
    CoreLayoutRequirement,
    LocalizedInsertPlacementRequirement,
    PlacementRequirementSet,
    PLACEMENT_REQUIREMENT_SCHEMA_VERSION,
    extract_placement_requirements,
)
from openmc_agent.plan_investigation.placement_preflight import (
    PLACEMENT_ASSEMBLY_TYPE_UNCOVERED,
    PLACEMENT_HOST_PATH_MISSING,
    PLACEMENT_INSERT_BINDING_MISSING,
    PLACEMENT_REQUIRED_UNIVERSE_MISSING,
    PLACEMENT_SOURCE_CRITICAL_UNRESOLVED,
    PlacementPreflightExecutionResult,
    run_evidence_qualified_placement_preflight,
)


class _FakeFacts:
    """Minimal duck-typed FactsPatch stand-in."""

    def __init__(
        self,
        *,
        assembly_type_counts: dict | None = None,
        core_lattice_size: tuple | None = None,
        fuel_variants: list | None = None,
        localized_inserts: list | None = None,
        model_scope: str = "multi_assembly_core",
    ) -> None:
        self.assembly_type_counts = assembly_type_counts or {}
        self.core_lattice_size = core_lattice_size
        self.fuel_variant_requirements = fuel_variants or []
        self.localized_insert_requirements = localized_inserts or []
        self.model_scope = model_scope


class _FakeInsert:
    """Duck-typed LocalizedInsertRequirement from FactsPatch."""

    def __init__(
        self, *, insert_kind: str, host_kind: str = "guide_tube",
        host_profile_id: str = "", insert_profile_id: str = "",
        required_segment_roles: list | None = None,
        required_universe_ids: list | None = None,
    ) -> None:
        self.insert_kind = insert_kind
        self.host_kind = host_kind
        self.host_profile_id = host_profile_id
        self.insert_profile_id = insert_profile_id
        self.required_segment_roles = required_segment_roles or []
        self.required_universe_ids = required_universe_ids or []


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


def test_compiler_produces_assembly_type_requirements() -> None:
    facts = _FakeFacts(assembly_type_counts={"C": 4, "E": 4, "R": 1})
    rs = extract_placement_requirements(accepted_facts=facts)
    assert len(rs.assembly_type_requirements) == 3
    types = {a.assembly_type_id for a in rs.assembly_type_requirements}
    assert types == {"C", "E", "R"}


def test_compiler_produces_core_layout_requirement() -> None:
    facts = _FakeFacts(
        assembly_type_counts={"C": 4},
        core_lattice_size=(3, 3),
    )
    rs = extract_placement_requirements(accepted_facts=facts)
    assert len(rs.core_layout_requirements) == 1
    cl = rs.core_layout_requirements[0]
    assert cl.lattice_shape == (3, 3)
    assert cl.assembly_type_counts == {"C": 4}


def test_compiler_produces_localized_insert_requirement() -> None:
    insert = _FakeInsert(
        insert_kind="control_rod",
        host_profile_id="guide_tube_prof",
        insert_profile_id="rcca_prof",
        required_segment_roles=["absorber"],
        required_universe_ids=["u_rcca"],
    )
    facts = _FakeFacts(localized_inserts=[insert])
    rs = extract_placement_requirements(accepted_facts=facts)
    assert len(rs.localized_insert_bindings) == 1
    li = rs.localized_insert_bindings[0]
    assert li.insert_kind == "control_rod"
    assert li.host_profile_id == "guide_tube_prof"
    assert li.required_segment_roles == ("absorber",)
    assert li.required_universe_ids == ("u_rcca",)


def test_compiler_flags_missing_host_profile_as_unresolved() -> None:
    """P0 rule: never derive 'control rod placed' from material existence."""

    insert = _FakeInsert(insert_kind="control_rod", host_profile_id="")
    facts = _FakeFacts(localized_inserts=[insert])
    rs = extract_placement_requirements(accepted_facts=facts)
    assert any("host_profile_id" in u for u in rs.unresolved_requirements)


def test_requirement_set_has_stable_hash() -> None:
    """Same inputs → same hash."""

    facts = _FakeFacts(assembly_type_counts={"C": 4})
    rs1 = extract_placement_requirements(accepted_facts=facts, ledger_hash="lh")
    rs2 = extract_placement_requirements(accepted_facts=facts, ledger_hash="lh")
    assert rs1.requirement_set_hash == rs2.requirement_set_hash


def test_requirement_set_hash_changes_on_different_inputs() -> None:
    rs1 = extract_placement_requirements(
        accepted_facts=_FakeFacts(assembly_type_counts={"C": 4}),
    )
    rs2 = extract_placement_requirements(
        accepted_facts=_FakeFacts(assembly_type_counts={"C": 5}),
    )
    assert rs1.requirement_set_hash != rs2.requirement_set_hash


def test_schema_version_is_set() -> None:
    assert PLACEMENT_REQUIREMENT_SCHEMA_VERSION == "1.0"
    rs = extract_placement_requirements(accepted_facts=_FakeFacts())
    assert rs.requirement_set_version == "1.0"


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def test_preflight_blocks_on_unresolved_source_critical() -> None:
    """Unresolved requirements block the gate."""

    facts = _FakeFacts(localized_inserts=[_FakeInsert(insert_kind="x", host_profile_id="")])
    rs = extract_placement_requirements(accepted_facts=facts)
    report = run_evidence_qualified_placement_preflight(
        requirement_set=rs, placement_patches={}, known_universe_ids=[],
    )
    assert not report.passed
    codes = [f.code for f in report.findings]
    assert PLACEMENT_SOURCE_CRITICAL_UNRESOLVED in codes


def test_preflight_blocks_on_missing_host_path() -> None:
    insert = _FakeInsert(insert_kind="control_rod", host_profile_id="")
    facts = _FakeFacts(localized_inserts=[insert])
    rs = extract_placement_requirements(accepted_facts=facts)
    report = run_evidence_qualified_placement_preflight(
        requirement_set=rs, placement_patches={}, known_universe_ids=[],
    )
    codes = [f.code for f in report.findings]
    assert PLACEMENT_HOST_PATH_MISSING in codes


def test_preflight_blocks_on_missing_insert_profile() -> None:
    insert = _FakeInsert(insert_kind="control_rod", insert_profile_id="")
    facts = _FakeFacts(localized_inserts=[insert])
    rs = extract_placement_requirements(accepted_facts=facts)
    report = run_evidence_qualified_placement_preflight(
        requirement_set=rs, placement_patches={}, known_universe_ids=[],
    )
    codes = [f.code for f in report.findings]
    assert PLACEMENT_INSERT_BINDING_MISSING in codes


def test_preflight_blocks_on_missing_required_universe() -> None:
    insert = _FakeInsert(
        insert_kind="control_rod",
        host_profile_id="host_prof",
        insert_profile_id="insert_prof",
        required_universe_ids=["u_rcca"],
    )
    facts = _FakeFacts(localized_inserts=[insert])
    rs = extract_placement_requirements(accepted_facts=facts)
    # u_rcca not in known_universe_ids → blocks.
    report = run_evidence_qualified_placement_preflight(
        requirement_set=rs, placement_patches={}, known_universe_ids=["u_other"],
    )
    codes = [f.code for f in report.findings]
    assert PLACEMENT_REQUIRED_UNIVERSE_MISSING in codes


def test_preflight_passes_when_no_blocking_findings() -> None:
    insert = _FakeInsert(
        insert_kind="control_rod",
        host_profile_id="host_prof",
        insert_profile_id="insert_prof",
        required_universe_ids=["u_rcca"],
    )
    facts = _FakeFacts(localized_inserts=[insert])
    rs = extract_placement_requirements(accepted_facts=facts)
    report = run_evidence_qualified_placement_preflight(
        requirement_set=rs, placement_patches={"core_layout": {"C": 1}},
        known_universe_ids=["u_rcca"],
    )
    assert report.passed


def test_execution_result_no_requirement_set_is_not_blocking() -> None:
    """No requirement set present (off mode) → not blocking."""

    result = PlacementPreflightExecutionResult(
        executed=False, requirement_set_present=False,
    )
    assert result.has_blocking_deterministic_finding is False


def test_execution_result_crash_is_blocking() -> None:
    """Crash → blocking."""

    result = PlacementPreflightExecutionResult(
        executed=False,
        requirement_set_present=True,
        execution_error="ValueError: bad",
        failure_code="placement.evidence_preflight_exception",
    )
    assert result.has_blocking_deterministic_finding is True


# ---------------------------------------------------------------------------
# Reactor-neutrality
# ---------------------------------------------------------------------------


def test_compiler_no_reactor_specific_branches() -> None:
    from pathlib import Path
    import openmc_agent.plan_investigation.placement_requirements as mod
    src = Path(mod.__file__).read_text()
    for forbidden in ("vera3", "vera4", "pwr", "bwr", "vver", "htgr", "sfr", "candu"):
        assert forbidden not in src.lower(), (
            f"reactor-specific term {forbidden!r} found"
        )
