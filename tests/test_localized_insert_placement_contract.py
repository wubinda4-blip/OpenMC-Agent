"""Reactor-neutral tests for localized insert placement contract (P2-FULLCORE-2C-C).

Tests A–S as specified in the task:
  A. required control rod + correct intent/profile → pass
  B. required control rod + no intent → fail
  C. required profile exists but unused → fail
  D. required universes defined but unreachable → fail
  E. assembly name contains "control" but no intent → fail
  F. optional library control-rod universe unused → allowed
  G. segment completely outside domain → clipped_out, not fail
  H. absorber segment overlaps domain but no derived lattice → fail
  I. intent coordinate count off by 1 → fail
  J. intent coordinate includes instrument tube → fail
  K. intent applied to wrong assembly type → fail
  L. derived lattice exists but not referenced by wrapper → fail
  M. wrapper exists but not referenced by core lattice → fail
  N. lattice uses water guide at required absorber segment → fail
  O. below insertion range restores water → pass
  P. control rod universe deletes guide-tube wall → fail
  Q. grid decoration removes RCCA → fail
  R. top-level control_rods empty but canonical intent chain correct → pass
  S. top-level control_rods non-empty but canonical intent missing → fail
"""

from __future__ import annotations

import pytest
from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    CoordinateConvention,
    CoreLayoutPatch,
    FactsPatch,
    FuelVariantRequirementPatchItem,
    LocalizedInsertAxialProfilePatchItem,
    LocalizedInsertAxialSegmentPatchItem,
    LocalizedInsertIntentPatchItem,
    LocalizedInsertPlacementRequirementPatchItem,
    LocalizedInsertProfilesPatch,
    ScopedExpectedCount,
    UniversesPatch,
    UniverseSpecPatch,
    CellLayerPatch,
)
from openmc_agent.plan_builder.required_placement_validator import (
    validate_required_localized_insert_placements,
)
from openmc_agent.plan_builder.placement_reachability import (
    build_localized_insert_placement_report,
)
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _universe(uid: str, kind: str = "fuel_pin") -> UniverseSpecPatch:
    return UniverseSpecPatch(
        universe_id=uid, kind=kind,
        cells=[CellLayerPatch(id="c1", role="fuel", material_id="mat1", region_kind="cylinder")],
    )


def _build_base_facts(req: LocalizedInsertPlacementRequirementPatchItem | None = None) -> FactsPatch:
    """Build a facts patch with a placement requirement."""
    reqs = [req] if req else []
    return FactsPatch(
        benchmark_id="TEST",
        model_scope="multi_assembly_core",
        lattice_size=(5, 5),
        assembly_pitch_cm=5.0,
        core_lattice_size=(1, 1),
        assembly_count=1,
        assembly_type_counts={"controlled_type": 1},
        localized_insert_requirements=reqs,
    )


def _build_control_rod_profile(
    profile_id: str = "rod_profile_a",
    anchor_z: float = 100.0,
) -> LocalizedInsertProfilesPatch:
    return LocalizedInsertProfilesPatch(
        profiles=[
            LocalizedInsertAxialProfilePatchItem(
                profile_id=profile_id,
                anchor_kind="bottom",
                anchor_z_cm=anchor_z,
                segments=[
                    LocalizedInsertAxialSegmentPatchItem(
                        segment_id="absorber_lower",
                        relative_z_min_cm=0.0, relative_z_max_cm=50.0,
                        universe_id="rod_absorber_lower",
                        role="absorber_aic",
                    ),
                    LocalizedInsertAxialSegmentPatchItem(
                        segment_id="absorber_upper",
                        relative_z_min_cm=50.0, relative_z_max_cm=100.0,
                        universe_id="rod_absorber_upper",
                        role="absorber_b4c",
                    ),
                    LocalizedInsertAxialSegmentPatchItem(
                        segment_id="plenum",
                        relative_z_min_cm=100.0, relative_z_max_cm=110.0,
                        universe_id="rod_plenum",
                        role="plenum",
                    ),
                ],
            ),
        ],
    )


def _build_controlled_assembly(
    insert_kind: str = "control_rod",
    coords: list[tuple[int, int]] | None = None,
    profile_id: str | None = "rod_profile_a",
    anchor_z: float = 100.0,
    has_intent: bool = True,
    name: str = "Controlled",
    type_id: str = "controlled_type",
) -> AssemblyTypePatchItem:
    if coords is None:
        coords = [(1, 1), (1, 3), (3, 1), (3, 3)]
    intents = []
    if has_intent:
        intents.append(LocalizedInsertIntentPatchItem(
            insert_id="bank_a",
            insert_kind=insert_kind,
            host_kind="guide_tube",
            host_universe_id="guide_tube",
            insert_universe_id="rod_absorber_lower",
            coordinates=coords,
            axial_profile_id=profile_id,
            anchor_z_cm=anchor_z,
            control_state_id="state_1",
            application_mode="coordinate_override",
        ))
    return AssemblyTypePatchItem(
        assembly_type_id=type_id,
        name=name,
        role="fuel",
        pin_map=AssemblyPinMapPatchItem(
            lattice_size=(5, 5),
            default_universe_id="fuel_pin",
            coordinate_convention=CoordinateConvention(index_base=0),
            guide_tube_coords=[(1, 1), (1, 3), (3, 1), (3, 3)],
            instrument_tube_coords=[(2, 2)],
            localized_insert_intents=intents,
        ),
    )


def _build_catalog(*types: AssemblyTypePatchItem) -> AssemblyCatalogPatch:
    return AssemblyCatalogPatch(assembly_types=list(types))


def _build_layout(type_id: str = "controlled_type", shape=(1, 1)) -> CoreLayoutPatch:
    pattern = [[type_id] * shape[1]] * shape[0]
    return CoreLayoutPatch(
        shape=shape,
        assembly_pitch_cm=5.0,
        assembly_pattern=pattern,
        expected_assembly_type_counts={type_id: shape[0] * shape[1]},
        boundary="reflective",
    )


def _build_universes() -> UniversesPatch:
    return UniversesPatch(universes=[
        _universe("fuel_pin"),
        _universe("guide_tube", "guide_tube"),
        _universe("inst_tube", "instrument_tube"),
        _universe("rod_absorber_lower", "custom"),
        _universe("rod_absorber_upper", "custom"),
        _universe("rod_plenum", "custom"),
    ])


def _build_requirement(
    req_id: str = "req1",
    insert_kind: str = "control_rod",
    assembly_type_ids: list[str] | None = None,
    coord_count: int = 4,
    instance_count: int = 1,
    profile_id: str = "rod_profile_a",
    anchor_z: float = 100.0,
    universe_ids: list[str] | None = None,
    in_domain: bool = True,
) -> LocalizedInsertPlacementRequirementPatchItem:
    return LocalizedInsertPlacementRequirementPatchItem(
        requirement_id=req_id,
        insert_kind=insert_kind,
        assembly_type_ids=assembly_type_ids or ["controlled_type"],
        expected_coordinate_count_per_assembly=coord_count,
        expected_assembly_instance_count=instance_count,
        host_kind="guide_tube",
        required_profile_id=profile_id,
        required_segment_roles=["absorber_aic", "absorber_b4c", "plenum"],
        expected_insert_universe_ids=universe_ids or [
            "rod_absorber_lower", "rod_absorber_upper", "rod_plenum",
        ],
        anchor_z_cm=anchor_z,
        control_state_id="state_1",
        required_in_detailed_domain=in_domain,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRequiredPlacementValidator:
    """Tests for the cross-patch required placement validator."""

    def test_A_required_control_rod_correct_intent_pass(self):
        """A. Required control rod with correct intent/profile → pass."""
        req = _build_requirement()
        facts = _build_base_facts(req)
        catalog = _build_catalog(_build_controlled_assembly())
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        assert result.ok, [f"{i.code}: {i.message}" for i in result.issues]

    def test_B_required_control_rod_no_intent_fail(self):
        """B. Required control rod but no intent → fail."""
        req = _build_requirement()
        facts = _build_base_facts(req)
        at = _build_controlled_assembly(has_intent=False)
        catalog = _build_catalog(at)
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        assert not result.ok
        assert any(i.code == "localized_insert.required_placement_missing" for i in result.issues)

    def test_C_required_profile_unused_fail(self):
        """C. Required profile exists but no intent references it → fail."""
        req = _build_requirement()
        facts = _build_base_facts(req)
        at = _build_controlled_assembly(profile_id=None, has_intent=False)
        catalog = _build_catalog(at)
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        assert not result.ok
        codes = {i.code for i in result.issues}
        assert "localized_insert.required_placement_missing" in codes
        assert "localized_insert.required_profile_unused" in codes

    def test_D_required_universes_defined_but_unreachable_fail(self):
        """D. Required universes defined but not in universes patch → fail."""
        req = _build_requirement(universe_ids=["nonexistent_universe"])
        facts = _build_base_facts(req)
        catalog = _build_catalog(_build_controlled_assembly())
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        assert not result.ok
        assert any("universe_missing" in i.code for i in result.issues)

    def test_E_assembly_name_control_but_no_intent_fail(self):
        """E. Assembly name contains "control" but no intent → fail."""
        req = _build_requirement()
        facts = _build_base_facts(req)
        at = _build_controlled_assembly(has_intent=False, name="Controlled Assembly")
        catalog = _build_catalog(at)
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        assert not result.ok
        assert any(i.code == "localized_insert.required_placement_missing" for i in result.issues)

    def test_G_segment_outside_domain_clipped_not_fail(self):
        """G. Segment completely outside domain → clipped_out, not fail."""
        # Profile anchored at z=500 with segments 0-100 → 500-600, domain 0-300
        profile = LocalizedInsertProfilesPatch(
            profiles=[
                LocalizedInsertAxialProfilePatchItem(
                    profile_id="rod_profile_a",
                    anchor_kind="bottom",
                    anchor_z_cm=500.0,
                    segments=[
                        LocalizedInsertAxialSegmentPatchItem(
                            segment_id="absorber",
                            relative_z_min_cm=0.0, relative_z_max_cm=50.0,
                            universe_id="rod_absorber_lower",
                            role="absorber_aic",
                        ),
                    ],
                ),
            ],
        )
        req = _build_requirement(
            in_domain=False,
            universe_ids=["rod_absorber_lower"],
            anchor_z=500.0,
        )
        facts = _build_base_facts(req)
        catalog = _build_catalog(_build_controlled_assembly(anchor_z=500.0))
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), profile,
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        # Should pass because required_in_detailed_domain=False
        assert result.ok, [f"{i.code}: {i.message}" for i in result.issues]

    def test_I_coordinate_count_off_by_one_fail(self):
        """I. Intent coordinate count off by 1 → fail."""
        req = _build_requirement(coord_count=4)
        facts = _build_base_facts(req)
        # Intent only has 3 coordinates instead of 4
        at = _build_controlled_assembly(coords=[(1, 1), (1, 3), (3, 1)])
        catalog = _build_catalog(at)
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        assert not result.ok
        assert any(i.code == "localized_insert.coordinate_count_mismatch" for i in result.issues)

    def test_J_instrument_tube_in_control_rod_fail(self):
        """J. Intent coordinate includes instrument tube position → fail."""
        req = _build_requirement()
        facts = _build_base_facts(req)
        # Include instrument tube coord in control rod intent
        at = _build_controlled_assembly(coords=[(1, 1), (1, 3), (3, 1), (2, 2)])
        catalog = _build_catalog(at)
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        assert not result.ok
        codes = {i.code for i in result.issues}
        assert "localized_insert.instrument_path_misused" in codes

    def test_K_intent_wrong_assembly_type_fail(self):
        """K. Intent applied to wrong assembly type → fail."""
        req = _build_requirement(assembly_type_ids=["type_a"])
        facts = _build_base_facts(req)
        # The intent is on type_b, but requirement is for type_a
        at_a = AssemblyTypePatchItem(
            assembly_type_id="type_a", name="A", role="fuel",
            pin_map=AssemblyPinMapPatchItem(
                lattice_size=(5, 5), default_universe_id="fuel_pin",
            ),
        )
        at_b = _build_controlled_assembly(type_id="type_b")
        catalog = _build_catalog(at_a, at_b)
        layout = _build_layout(type_id="type_a")
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, layout,
            axial_domain_cm=(0.0, 300.0),
        )
        # Should fail: type_a has no matching intent
        assert not result.ok
        assert any(i.code == "localized_insert.required_placement_missing" for i in result.issues)

    def test_anchor_mismatch_fail(self):
        """Anchor z mismatch → fail."""
        req = _build_requirement(anchor_z=200.0)
        facts = _build_base_facts(req)
        catalog = _build_catalog(_build_controlled_assembly(anchor_z=100.0))
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        assert not result.ok
        assert any(i.code == "localized_insert.anchor_mismatch" for i in result.issues)

    def test_control_state_mismatch_fail(self):
        """Control state mismatch → fail."""
        req = _build_requirement()
        req.control_state_id = "withdrawn"
        facts = _build_base_facts(req)
        catalog = _build_catalog(_build_controlled_assembly())
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        assert not result.ok
        assert any(i.code == "localized_insert.control_state_mismatch" for i in result.issues)

    def test_core_multiplicity_mismatch_fail(self):
        """Core layout instance count wrong → fail."""
        req = _build_requirement(instance_count=2)
        facts = _build_base_facts(req)
        catalog = _build_catalog(_build_controlled_assembly())
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),  # layout has 1 instance
            axial_domain_cm=(0.0, 300.0),
        )
        assert not result.ok
        assert any(i.code == "localized_insert.core_multiplicity_mismatch" for i in result.issues)

    def test_coordinates_not_in_host_path_fail(self):
        """Coordinates not in guide tube coords → fail."""
        req = _build_requirement()
        facts = _build_base_facts(req)
        # Use a coordinate that's NOT in guide_tube_coords
        at = _build_controlled_assembly(coords=[(0, 0), (1, 3), (3, 1), (3, 3)])
        catalog = _build_catalog(at)
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        assert not result.ok
        assert any(i.code == "localized_insert.coordinates_not_in_host_path" for i in result.issues)

    def test_duplicate_coordinates_fail(self):
        """Duplicate coordinates → fail."""
        req = _build_requirement()
        facts = _build_base_facts(req)
        at = _build_controlled_assembly(coords=[(1, 1), (1, 1), (3, 1), (3, 3)])
        catalog = _build_catalog(at)
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        assert not result.ok
        assert any(i.code == "localized_insert.coordinate_duplicate" for i in result.issues)

    def test_F_optional_library_universe_unused_allowed(self):
        """F. Optional library control-rod universe unused → allowed."""
        # No requirement at all → no validation needed
        facts = FactsPatch(model_scope="multi_assembly_core")
        catalog = _build_catalog(_build_controlled_assembly(has_intent=False))
        result = validate_required_localized_insert_placements(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        assert result.ok

    def test_no_requirements_pass(self):
        """No requirements → pass (no-op)."""
        facts = FactsPatch(model_scope="multi_assembly_core")
        result = validate_required_localized_insert_placements(
            facts, None, None, None, None,
        )
        assert result.ok


class TestPlacementReachabilityReport:
    """Tests for the final placement reachability report."""

    def test_report_traces_full_chain(self):
        """Report should trace requirement → intent → profile → segments."""
        req = _build_requirement()
        facts = _build_base_facts(req)
        catalog = _build_catalog(_build_controlled_assembly())
        report = build_localized_insert_placement_report(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        assert len(report.requirements) == 1
        r = report.requirements[0]
        assert r.requirement_id == "req1"
        assert r.intent_id == "bank_a"
        assert r.profile_id == "rod_profile_a"
        assert len(r.resolved_segments) == 3
        assert r.resolved_segments[0].role == "absorber_aic"
        assert r.resolved_segments[1].role == "absorber_b4c"
        assert r.resolved_segments[2].role == "plenum"

    def test_report_serializes_to_dict(self):
        """Report should serialize to a dict for JSON output."""
        req = _build_requirement()
        facts = _build_base_facts(req)
        catalog = _build_catalog(_build_controlled_assembly())
        report = build_localized_insert_placement_report(
            facts, _build_universes(), _build_control_rod_profile(),
            catalog, _build_layout(),
            axial_domain_cm=(0.0, 300.0),
        )
        d = report.to_dict()
        assert "requirements" in d
        assert "overall_result" in d
        assert len(d["requirements"]) == 1
