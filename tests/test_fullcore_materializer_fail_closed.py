"""Tests for structured fail-closed materialization issues (P2-FULLCORE-2D-A/9)."""

from __future__ import annotations

from openmc_agent.plan_builder.axial_state_materializer import (
    MaterializationIssue,
    materialize_concrete_axial_states,
    ConcreteAxialStateResult,
)
from openmc_agent.plan_builder.hierarchical_assembler import AxialSegment
from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    CoreLayoutPatch,
    LocalizedInsertIntentPatchItem,
)
from openmc_agent.schemas import LatticeSpec


def _catalog() -> AssemblyCatalogPatch:
    return AssemblyCatalogPatch(assembly_types=[
        AssemblyTypePatchItem(
            assembly_type_id="fuel", name="fuel", role="fuel",
            pin_map=AssemblyPinMapPatchItem(lattice_size=(3, 3), default_universe_id="fuel_pin"),
        ),
    ])


def _layout() -> CoreLayoutPatch:
    return CoreLayoutPatch(
        shape=(1, 1), assembly_pitch_cm=3.78,
        assembly_pattern=[["fuel"]], boundary="reflective",
    )


def _base_lats() -> dict[str, LatticeSpec]:
    return {"fuel": LatticeSpec(
        id="assembly_lattice__fuel", name="test", kind="rect",
        pitch_cm=(1.26, 1.26), outer_universe_id="mod_outer",
        universe_pattern=[["fuel_pin"] * 3] * 3, shape=(3, 3),
    )}


def _base_uvs() -> dict[str, str]:
    return {"fuel": "assembly_universe__fuel"}


class TestMaterializationIssue:
    def test_issue_is_typed(self):
        issue = MaterializationIssue(
            code="test.code", severity="error", message="test",
        )
        assert issue.code == "test.code"
        assert issue.severity == "error"
        assert issue.message == "test"
        assert issue.segment_id is None
        assert issue.assembly_type_id is None


class TestFailClosed:
    def test_base_lattice_missing_reports_error(self):
        """Missing base lattice for a type should fail-closed."""
        catalog = AssemblyCatalogPatch(assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="missing_type", name="missing", role="fuel",
                pin_map=AssemblyPinMapPatchItem(lattice_size=(3, 3), default_universe_id="x"),
            ),
        ])
        segments = [
            AxialSegment(segment_id="s0", z_min_cm=0.0, z_max_cm=5.0, fill_mode="detailed_core"),
        ]
        result = materialize_concrete_axial_states(
            catalog, _layout(), segments, {}, {},
        )
        codes = [i.code for i in result.issues]
        assert "fullcore.base_lattice_missing" in codes
        assert result.has_errors

    def test_whole_plane_missing_fill_id_reports_error(self):
        segments = [
            AxialSegment(
                segment_id="s0", z_min_cm=0.0, z_max_cm=5.0,
                fill_mode="whole_plane_material", base_fill_id=None,
            ),
        ]
        result = materialize_concrete_axial_states(
            _catalog(), _layout(), segments, _base_lats(), _base_uvs(),
        )
        codes = [i.code for i in result.issues]
        assert "fullcore.whole_plane_fill_missing" in codes

    def test_whole_plane_missing_material_ref_reports_error(self):
        segments = [
            AxialSegment(
                segment_id="s0", z_min_cm=0.0, z_max_cm=5.0,
                fill_mode="whole_plane_material", base_fill_id="nonexistent",
            ),
        ]
        result = materialize_concrete_axial_states(
            _catalog(), _layout(), segments, _base_lats(), _base_uvs(),
            known_material_ids={"water", "fuel"},
        )
        codes = [i.code for i in result.issues]
        assert "fullcore.whole_plane_ref_missing" in codes

    def test_coordinate_out_of_bounds_reports_error(self):
        """Insert coordinates outside lattice bounds should fail-closed."""
        catalog = AssemblyCatalogPatch(assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="fuel", name="f", role="fuel",
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(3, 3), default_universe_id="fuel_pin",
                    localized_insert_intents=[
                        LocalizedInsertIntentPatchItem(
                            insert_id="bad", insert_kind="absorber_insert",
                            insert_universe_id="abs",
                            coordinates=[(10, 10)],  # out of bounds for 3×3
                            z_min_cm=0.0, z_max_cm=10.0,
                        ),
                    ],
                ),
            ),
        ])
        segments = [
            AxialSegment(segment_id="s0", z_min_cm=0.0, z_max_cm=5.0, fill_mode="detailed_core"),
        ]
        result = materialize_concrete_axial_states(
            catalog, _layout(), segments, _base_lats(), _base_uvs(),
        )
        codes = [i.code for i in result.issues]
        assert "fullcore.coordinate_out_of_bounds" in codes

    def test_has_errors_property(self):
        result = ConcreteAxialStateResult()
        assert not result.has_errors
        result.issues.append(MaterializationIssue(code="x", severity="error", message="m"))
        assert result.has_errors

    def test_no_errors_for_valid_model(self):
        """A valid model should have no errors."""
        segments = [
            AxialSegment(segment_id="s0", z_min_cm=0.0, z_max_cm=5.0, fill_mode="detailed_core"),
        ]
        result = materialize_concrete_axial_states(
            _catalog(), _layout(), segments, _base_lats(), _base_uvs(),
        )
        assert not result.has_errors
