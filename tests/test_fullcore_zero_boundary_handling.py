"""Tests for zero-boundary truthiness fix (P2-FULLCORE-2D-A).

Regression tests for the bug where z_min=0.0 or z_max=0.0 was
treated as None (missing) by the ``or`` truthiness pattern.
"""

from __future__ import annotations

from openmc_agent.plan_builder.hierarchical_assembler import (
    AxialSegment,
    compile_global_axial_segments,
)
from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    FactsPatch,
    LocalizedInsertIntentPatchItem,
)
from openmc_agent.plan_builder.axial_state_materializer import (
    _get_active_inserts_for_segment,
    materialize_concrete_axial_states,
    ConcreteAxialStateResult,
)
from openmc_agent.plan_builder.patches import CoreLayoutPatch
from openmc_agent.schemas import LatticeSpec


def _catalog_with_simple_insert(z_min: float | None, z_max: float | None):
    """Build a catalog with one insert at the given z range."""
    return AssemblyCatalogPatch(assembly_types=[
        AssemblyTypePatchItem(
            assembly_type_id="test_type",
            name="test",
            role="fuel",
            pin_map=AssemblyPinMapPatchItem(
                lattice_size=(3, 3),
                default_universe_id="fuel_pin",
                localized_insert_intents=[
                    LocalizedInsertIntentPatchItem(
                        insert_id="test_insert",
                        insert_kind="absorber_insert",
                        host_kind="guide_tube",
                        insert_universe_id="absorber_uv",
                        coordinates=[(0, 0)],
                        z_min_cm=z_min,
                        z_max_cm=z_max,
                    ),
                ],
            ),
        ),
    ])


class TestZeroBoundaryTruthiness:
    def test_z_min_zero_not_treated_as_none(self):
        """An insert with z_min=0.0 should be active in segments starting at 0.0."""
        catalog = _catalog_with_simple_insert(z_min=0.0, z_max=10.0)
        segment = AxialSegment(segment_id="s0", z_min_cm=0.0, z_max_cm=5.0)

        active = _get_active_inserts_for_segment(segment, catalog)

        assert "test_type" in active
        assert len(active["test_type"]) == 1

    def test_z_max_zero_insert_invalid_range(self):
        """An insert with z_min=5.0, z_max=0.0 should NOT be active in any valid segment."""
        catalog = _catalog_with_simple_insert(z_min=5.0, z_max=0.0)
        segment = AxialSegment(segment_id="s0", z_min_cm=0.0, z_max_cm=3.0)

        active = _get_active_inserts_for_segment(segment, catalog)

        # The insert has z_min=5 > z_max=0, which is an invalid range.
        # For a segment [0, 3], z_min=5 means the insert doesn't start until 5.
        # So it should NOT be active.
        assert "test_type" not in active

    def test_negative_z_min(self):
        """An insert with negative z_min should be handled correctly."""
        catalog = _catalog_with_simple_insert(z_min=-10.0, z_max=5.0)
        segment = AxialSegment(segment_id="s0", z_min_cm=-5.0, z_max_cm=0.0)

        active = _get_active_inserts_for_segment(segment, catalog)

        assert "test_type" in active

    def test_segment_crossing_zero(self):
        """A segment crossing z=0 should match inserts on both sides."""
        catalog = _catalog_with_simple_insert(z_min=-5.0, z_max=5.0)
        segment = AxialSegment(segment_id="s0", z_min_cm=-1.0, z_max_cm=1.0)

        active = _get_active_inserts_for_segment(segment, catalog)

        assert "test_type" in active

    def test_compile_segments_with_zero_boundary(self):
        """Compile segments with an insert boundary at z=0.0."""
        facts = FactsPatch(axial_domain_cm=(-5.0, 10.0))
        catalog = _catalog_with_simple_insert(z_min=0.0, z_max=10.0)

        segments = compile_global_axial_segments(facts, catalog)

        # Should have breakpoints at -5.0, 0.0, 10.0
        z_mins = [s.z_min_cm for s in segments]
        assert 0.0 in z_mins

    def test_materializer_with_zero_z_min(self):
        """Materializer should handle z_min=0.0 insert correctly."""
        catalog = _catalog_with_simple_insert(z_min=0.0, z_max=10.0)
        layout = CoreLayoutPatch(
            shape=(1, 1), assembly_pitch_cm=3.78,
            assembly_pattern=[["test_type"]], boundary="reflective",
        )
        segments = [
            AxialSegment(segment_id="s0", z_min_cm=0.0, z_max_cm=5.0, fill_mode="detailed_core"),
        ]
        base_lats = {
            "test_type": LatticeSpec(
                id="assembly_lattice__test_type", name="test", kind="rect",
                pitch_cm=(1.26, 1.26), outer_universe_id="mod_outer",
                universe_pattern=[["fuel_pin"] * 3] * 3, shape=(3, 3),
            ),
        }
        base_uvs = {"test_type": "assembly_universe__test_type"}

        result = materialize_concrete_axial_states(
            catalog, layout, segments, base_lats, base_uvs,
        )

        # Should have derived lattice with the insert
        assert len(result.derived_pin_lattices) == 1
        assert result.derived_pin_lattices[0].universe_pattern[0][0] == "absorber_uv"
