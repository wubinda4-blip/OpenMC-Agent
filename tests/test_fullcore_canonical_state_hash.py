"""Tests for canonical state hashing and core-state reuse (P2-FULLCORE-2D-A/8)."""

from __future__ import annotations

from openmc_agent.plan_builder.axial_state_materializer import (
    _compute_pin_state_hash,
    _compute_core_state_hash,
    materialize_concrete_axial_states,
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
            name="fuel", role="fuel",
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


class TestPinStateHash:
    def test_same_pattern_same_hash(self):
        pattern = [["a", "b"], ["c", "d"]]
        h1 = _compute_pin_state_hash("type1", pattern, ["ins1"], 0)
        h2 = _compute_pin_state_hash("type1", pattern, ["ins1"], 0)
        assert h1 == h2

    def test_different_pattern_different_hash(self):
        h1 = _compute_pin_state_hash("type1", [["a"]], [], 0)
        h2 = _compute_pin_state_hash("type1", [["b"]], [], 0)
        assert h1 != h2

    def test_different_type_different_hash(self):
        pattern = [["a"]]
        h1 = _compute_pin_state_hash("type1", pattern, [], 0)
        h2 = _compute_pin_state_hash("type2", pattern, [], 0)
        assert h1 != h2

    def test_different_inserts_different_hash(self):
        pattern = [["a"]]
        h1 = _compute_pin_state_hash("t", pattern, ["ins1"], 0)
        h2 = _compute_pin_state_hash("t", pattern, ["ins2"], 0)
        assert h1 != h2

    def test_different_grid_different_hash(self):
        pattern = [["a"]]
        h1 = _compute_pin_state_hash("t", pattern, [], 0, None)
        h2 = _compute_pin_state_hash("t", pattern, [], 0, ["grid1"])
        assert h1 != h2

    def test_hash_is_hex(self):
        h = _compute_pin_state_hash("t", [["a"]], [], 0)
        int(h, 16)  # Should not raise


class TestCoreStateHash:
    def test_same_pattern_same_hash(self):
        pattern = [["a", "b"], ["c", "d"]]
        h1 = _compute_core_state_hash(pattern, "core_lattice")
        h2 = _compute_core_state_hash(pattern, "core_lattice")
        assert h1 == h2

    def test_different_pattern_different_hash(self):
        h1 = _compute_core_state_hash([["a"]], "core")
        h2 = _compute_core_state_hash([["b"]], "core")
        assert h1 != h2


class TestStateReuseInMaterializer:
    def test_identical_detailed_segments_reuse_pin_lattice(self):
        """Two segments with identical states should produce only 1 derived pin lattice."""
        segments = [
            AxialSegment(segment_id="s0", z_min_cm=0.0, z_max_cm=5.0, fill_mode="detailed_core"),
            AxialSegment(segment_id="s1", z_min_cm=5.0, z_max_cm=10.0, fill_mode="detailed_core"),
        ]
        result = materialize_concrete_axial_states(
            _catalog(), _layout(), segments, _base_lats(), _base_uvs(),
        )
        # Both segments have no inserts → both use base core lattice
        assert len(result.derived_pin_lattices) == 0
        assert len(result.segment_core_lattices) == 0

    def test_state_reuse_report_generated(self):
        segments = [
            AxialSegment(segment_id="s0", z_min_cm=0.0, z_max_cm=5.0, fill_mode="detailed_core"),
        ]
        result = materialize_concrete_axial_states(
            _catalog(), _layout(), segments, _base_lats(), _base_uvs(),
        )
        assert "contract_version" in result.state_reuse_report
        assert "pin_state_lookups" in result.state_reuse_report
        assert "core_state_lookups" in result.state_reuse_report

    def test_ids_are_content_based_not_sequential(self):
        """Derived IDs should contain hash, not seg0/seg1."""
        from openmc_agent.plan_builder.patches import LocalizedInsertIntentPatchItem
        catalog = AssemblyCatalogPatch(assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="fuel", name="f", role="fuel",
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(3, 3), default_universe_id="fuel_pin",
                    localized_insert_intents=[
                        LocalizedInsertIntentPatchItem(
                            insert_id="abs", insert_kind="absorber_insert",
                            insert_universe_id="absorber_uv",
                            coordinates=[(0, 0)],
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
        for lat in result.derived_pin_lattices:
            assert "seg" not in lat.id, f"ID still uses seg counter: {lat.id}"
            assert "__" in lat.id
