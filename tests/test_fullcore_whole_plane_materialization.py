"""Tests for whole-plane segment materialization (P2-FULLCORE-2D-A)."""

from __future__ import annotations

from openmc_agent.plan_builder.axial_state_materializer import (
    materialize_concrete_axial_states,
    ConcreteAxialStateResult,
)
from openmc_agent.plan_builder.hierarchical_assembler import AxialSegment
from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    CoreLayoutPatch,
)
from openmc_agent.schemas import LatticeSpec


def _catalog() -> AssemblyCatalogPatch:
    return AssemblyCatalogPatch(assembly_types=[
        AssemblyTypePatchItem(
            assembly_type_id="fuel",
            name="fuel",
            role="fuel",
            pin_map=AssemblyPinMapPatchItem(
                lattice_size=(3, 3),
                default_universe_id="fuel_pin",
            ),
        ),
    ])


def _layout() -> CoreLayoutPatch:
    return CoreLayoutPatch(
        shape=(1, 1),
        assembly_pitch_cm=3.78,
        assembly_pattern=[["fuel"]],
        boundary="reflective",
    )


def _base_lats() -> dict[str, LatticeSpec]:
    return {
        "fuel": LatticeSpec(
            id="assembly_lattice__fuel",
            name="pin lattice fuel",
            kind="rect",
            pitch_cm=(1.26, 1.26),
            outer_universe_id="moderator_outer",
            universe_pattern=[["fuel_pin"] * 3] * 3,
            shape=(3, 3),
        ),
    }


def _base_uvs() -> dict[str, str]:
    return {"fuel": "assembly_universe__fuel"}


class TestWholePlaneMaterialization:
    def test_whole_plane_no_derived_lattices(self):
        """Whole-plane material fill should not create any pin/core lattices."""
        segments = [
            AxialSegment(
                segment_id="s0", z_min_cm=-55.0, z_max_cm=-5.0,
                fill_mode="whole_plane_material", base_fill_id="water",
                base_role="lower_moderator_buffer",
            ),
        ]
        result = materialize_concrete_axial_states(
            _catalog(), _layout(), segments, _base_lats(), _base_uvs(),
        )
        assert len(result.axial_layers) == 1
        assert result.axial_layers[0].fill.type == "material"
        assert result.axial_layers[0].fill.id == "water"
        assert len(result.derived_pin_lattices) == 0
        assert len(result.segment_core_lattices) == 0
        assert len(result.derived_wrapper_universes) == 0

    def test_whole_plane_universe_fill(self):
        segments = [
            AxialSegment(
                segment_id="s0", z_min_cm=0.0, z_max_cm=5.0,
                fill_mode="whole_plane_universe", base_fill_id="reflector_uv",
                base_role="reflector",
            ),
        ]
        result = materialize_concrete_axial_states(
            _catalog(), _layout(), segments, _base_lats(), _base_uvs(),
        )
        assert result.axial_layers[0].fill.type == "universe"
        assert result.axial_layers[0].fill.id == "reflector_uv"

    def test_whole_plane_no_double_counting(self):
        """Whole-plane slab must not contain any lattice fills."""
        segments = [
            AxialSegment(
                segment_id="s0", z_min_cm=-5.0, z_max_cm=0.0,
                fill_mode="whole_plane_material", base_fill_id="core_plate_mix",
                base_role="lower_core_plate",
            ),
            AxialSegment(
                segment_id="s1", z_min_cm=0.0, z_max_cm=10.0,
                fill_mode="detailed_core",
            ),
        ]
        result = materialize_concrete_axial_states(
            _catalog(), _layout(), segments, _base_lats(), _base_uvs(),
        )
        assert len(result.axial_layers) == 2
        assert result.axial_layers[0].fill.type == "material"
        assert result.axial_layers[1].fill.type == "lattice"
        # No core lattice for the whole-plane segment
        assert len(result.segment_core_lattices) <= 1

    def test_whole_plane_missing_ref_reports_error(self):
        """Missing material ref for whole-plane should report an error."""
        segments = [
            AxialSegment(
                segment_id="s0", z_min_cm=0.0, z_max_cm=5.0,
                fill_mode="whole_plane_material", base_fill_id="nonexistent",
            ),
        ]
        result = materialize_concrete_axial_states(
            _catalog(), _layout(), segments, _base_lats(), _base_uvs(),
            known_material_ids={"water", "fuel_r1"},
        )
        codes = [i.code for i in result.issues]
        assert "fullcore.whole_plane_ref_missing" in codes
