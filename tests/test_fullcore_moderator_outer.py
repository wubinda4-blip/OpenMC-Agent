"""Tests for moderator outer universe and assembly gap (P2-FULLCORE-2B).

Verifies that pin lattice outer is a real moderator universe (not fuel),
and that assembly gap is correctly calculated and filled with coolant.
"""

from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    CoreLayoutPatch,
)
from openmc_agent.plan_builder.hierarchical_assembler import (
    build_hierarchical_core_plan,
    ensure_moderator_outer_universe,
    assemble_assembly_templates,
)


def _make_catalog():
    return AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="type_a",
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(17, 17),
                    default_universe_id="fuel_cell",
                    guide_tube_coords=[(2, 5)],
                ),
            ),
        ],
    )


def _make_layout():
    return CoreLayoutPatch(
        shape=(2, 2),
        assembly_pitch_cm=21.50,
        assembly_pattern=[["type_a", "type_a"], ["type_a", "type_a"]],
        boundary="reflective",
    )


def test_moderator_universe_created():
    """ensure_moderator_outer_universe creates a valid UniverseSpec."""
    universe, cell = ensure_moderator_outer_universe(
        moderator_universe_id="mod_outer",
        coolant_material_id="water",
    )
    assert universe.id == "mod_outer"
    assert len(universe.cell_ids) == 1
    assert cell.fill_type == "material"
    assert cell.fill_id == "water"
    assert cell.component_role == "coolant"


def test_pin_lattice_outer_is_moderator():
    """Pin lattice outer_universe_id must be moderator, not fuel."""
    result = build_hierarchical_core_plan(
        _make_catalog(), _make_layout(), facts=None,
        pitch_cm=1.26,
        moderator_universe_id="moderator_outer",
        coolant_material_id="water",
    )
    pin_lattice = result.pin_lattices[0]
    assert pin_lattice.outer_universe_id == "moderator_outer"
    assert pin_lattice.outer_universe_id != "fuel_cell"


def test_pin_lattice_outer_is_not_fuel():
    """Pin lattice outer must NOT be the default_universe_id (fuel)."""
    catalog = _make_catalog()
    layout = _make_layout()
    result = build_hierarchical_core_plan(catalog, layout, facts=None)
    for lat in result.pin_lattices:
        assert lat.outer_universe_id != "fuel_cell"


def test_moderator_universe_in_result():
    """Result must include moderator universe and cell."""
    result = build_hierarchical_core_plan(
        _make_catalog(), _make_layout(), facts=None,
    )
    uv_ids = {u.id for u in result.assembly_universes}
    assert "moderator_outer" in uv_ids
    cell_ids = {c.id for c in result.assembly_wrapper_cells}
    assert "moderator_outer_cell" in cell_ids


def test_assembly_gap_calculation():
    """Assembly gap = assembly_pitch - pin_lattice_width."""
    result = build_hierarchical_core_plan(
        _make_catalog(), _make_layout(), facts=None,
        pitch_cm=1.26,
    )
    # 17 × 1.26 = 21.42, assembly pitch = 21.50
    # gap = 21.50 - 21.42 = 0.08
    assert abs(result.summary["assembly_gap_cm"] - 0.08) < 0.01
    assert abs(result.summary["assembly_half_gap_cm"] - 0.04) < 0.01


def test_assembly_gap_report_in_summary():
    """Summary must include gap information."""
    result = build_hierarchical_core_plan(
        _make_catalog(), _make_layout(), facts=None,
        pitch_cm=1.26,
    )
    assert "assembly_gap_cm" in result.summary
    assert "assembly_half_gap_cm" in result.summary
    assert "pin_lattice_outer_is_fuel" in result.summary
    assert result.summary["pin_lattice_outer_is_fuel"] is False


def test_core_lattice_outer_is_moderator():
    """Core lattice outer should be moderator (not a fuel universe)."""
    result = build_hierarchical_core_plan(
        _make_catalog(), _make_layout(), facts=None,
    )
    core_lat = result.core_lattices[0]
    assert core_lat.outer_universe_id != "fuel_cell"


def test_moderator_outer_not_material_id():
    """The outer_universe_id must be a universe ID, not a material ID."""
    result = build_hierarchical_core_plan(
        _make_catalog(), _make_layout(), facts=None,
        moderator_universe_id="moderator_outer",
        coolant_material_id="water",
    )
    for lat in result.pin_lattices:
        # "water" is a material ID — it should NOT be used as outer_universe_id
        assert lat.outer_universe_id != "water"
        assert lat.outer_universe_id == "moderator_outer"
