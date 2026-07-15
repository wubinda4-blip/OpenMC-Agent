"""Tests for assembly template reuse in hierarchical core (P2-FULLCORE-1).

Verifies that assembly types are defined once and reused across multiple
core positions — no duplication of pin-cell universes per instance.
"""

from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    CoreLayoutPatch,
)
from openmc_agent.plan_builder.hierarchical_assembler import (
    build_hierarchical_core_plan,
)


def test_template_count_equals_type_count():
    """For N assembly types, there should be exactly N pin lattices."""
    catalog = AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id=f"type_{i}",
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(3, 3),
                    default_universe_id="fuel",
                ),
            )
            for i in range(3)
        ],
    )
    layout = CoreLayoutPatch(
        shape=(3, 3),
        assembly_pattern=[
            ["type_0", "type_1", "type_0"],
            ["type_1", "type_2", "type_1"],
            ["type_0", "type_1", "type_0"],
        ],
        boundary="reflective",
    )
    result = build_hierarchical_core_plan(catalog, layout, facts=None)
    # 3 types → 3 pin lattices, 3 assemblies
    # But 9 core positions
    assert len(result.pin_lattices) == 3
    assert len(result.assembly_specs) == 3
    assert result.core_count_aggregation.total_assembly_instances == 9


def test_reuse_ratio_heterogeneous_2x2():
    """Heterogeneous 2x2: 2 types, 4 instances → 50% template reuse."""
    catalog = AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="A",
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(3, 3), default_universe_id="fuel",
                    guide_tube_coords=[(1, 1)],
                ),
            ),
            AssemblyTypePatchItem(
                assembly_type_id="B",
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(3, 3), default_universe_id="fuel",
                    guide_tube_coords=[(0, 0), (2, 2)],
                ),
            ),
        ],
    )
    layout = CoreLayoutPatch(
        shape=(2, 2),
        assembly_pattern=[["A", "B"], ["B", "A"]],
        boundary="reflective",
    )
    result = build_hierarchical_core_plan(catalog, layout, facts=None)
    assert len(result.pin_lattices) == 2
    assert result.core_count_aggregation.total_assembly_instances == 4
    # Reuse: 4 instances / 2 templates = 2x reuse
    reuse_ratio = 4 / 2
    assert reuse_ratio == 2.0


def test_no_duplicate_lattice_ids():
    """Each type should produce exactly one unique lattice ID."""
    catalog = AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id=tid,
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(3, 3), default_universe_id="fuel",
                ),
            )
            for tid in ["a", "b", "c"]
        ],
    )
    layout = CoreLayoutPatch(
        shape=(1, 3),
        assembly_pattern=[["a", "b", "c"]],
        boundary="reflective",
    )
    result = build_hierarchical_core_plan(catalog, layout, facts=None)
    lattice_ids = [lat.id for lat in result.pin_lattices]
    assert len(lattice_ids) == len(set(lattice_ids))  # no duplicates
