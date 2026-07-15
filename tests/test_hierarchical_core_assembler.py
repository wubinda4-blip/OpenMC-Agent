"""Tests for hierarchical core assembler (P2-FULLCORE-1)."""

from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    CoreLayoutPatch,
)
from openmc_agent.plan_builder.hierarchical_assembler import (
    build_hierarchical_core_plan,
    lift_single_pin_map_to_catalog,
    assemble_assembly_templates,
    assemble_core_lattice,
)


def _make_catalog():
    return AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="type_a",
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(3, 3),
                    default_universe_id="fuel",
                    guide_tube_coords=[(1, 1)],
                ),
            ),
            AssemblyTypePatchItem(
                assembly_type_id="type_b",
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(3, 3),
                    default_universe_id="fuel",
                    guide_tube_coords=[(0, 0), (2, 2)],
                ),
            ),
        ],
    )


def _make_layout():
    return CoreLayoutPatch(
        shape=(2, 2),
        assembly_pitch_cm=21.5,
        assembly_pattern=[["type_a", "type_b"], ["type_b", "type_a"]],
        boundary="reflective",
    )


def test_build_hierarchical_core_plan():
    catalog = _make_catalog()
    layout = _make_layout()
    result = build_hierarchical_core_plan(catalog, layout, facts=None)
    assert len(result["pin_lattices"]) == 2
    assert len(result["assemblies"]) == 2
    assert result["core_lattice"] is not None
    assert result["core_spec"] is not None


def test_pin_lattice_ids_per_type():
    catalog = _make_catalog()
    layout = _make_layout()
    result = build_hierarchical_core_plan(catalog, layout, facts=None)
    lattice_ids = [lat.id for lat in result["pin_lattices"]]
    assert "assembly_lattice__type_a" in lattice_ids
    assert "assembly_lattice__type_b" in lattice_ids


def test_core_lattice_shape():
    catalog = _make_catalog()
    layout = _make_layout()
    result = build_hierarchical_core_plan(catalog, layout, facts=None)
    core_lat = result["core_lattice"]
    assert core_lat.shape == (2, 2)
    assert len(core_lat.universe_pattern) == 2
    assert len(core_lat.universe_pattern[0]) == 2


def test_core_count_aggregation():
    catalog = _make_catalog()
    layout = _make_layout()
    result = build_hierarchical_core_plan(catalog, layout, facts=None)
    agg = result["core_count_aggregation"]
    assert agg.total_assembly_instances == 4
    # type_a: 8 fuel × 2 = 16, type_b: 7 fuel × 2 = 14 → total = 30
    assert agg.core_total_for_role("fuel_pin") == 30


def test_lift_single_pin_map_to_catalog():
    from openmc_agent.plan_builder.patches import PinMapPatch
    pin_map = PinMapPatch(
        lattice_size=(3, 3),
        default_universe_id="fuel",
        guide_tube_coords=[(1, 1)],
    )
    catalog = lift_single_pin_map_to_catalog(pin_map)
    assert len(catalog.assembly_types) == 1
    assert catalog.assembly_types[0].assembly_type_id == "assembly_type_1"
    assert catalog.assembly_types[0].pin_map.lattice_size == (3, 3)
    assert catalog.source_note is not None


def test_assemble_assembly_templates_returns_summaries():
    catalog = _make_catalog()
    lattices, assemblies, summaries, issues = assemble_assembly_templates(catalog)
    assert len(lattices) == 2
    assert len(assemblies) == 2
    assert "type_a" in summaries
    assert "type_b" in summaries
    assert summaries["type_a"].fuel_pin_count == 8
    assert summaries["type_b"].fuel_pin_count == 7


def test_core_lattice_assembly_universe_ids():
    catalog = _make_catalog()
    layout = _make_layout()
    assembly_uvs = {"type_a": "universe_a", "type_b": "universe_b"}
    core_lat = assemble_core_lattice(layout, assembly_uvs)
    assert core_lat.universe_pattern[0][0] == "universe_a"
    assert core_lat.universe_pattern[0][1] == "universe_b"
