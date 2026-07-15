"""Tests for base lattice purity — no localized inserts in base pin lattice (P2-FULLCORE-2A)."""

from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    LocalizedInsertIntentPatchItem,
)
from openmc_agent.plan_builder.hierarchical_assembler import (
    _expand_assembly_pin_map,
    build_hierarchical_core_plan,
)


def test_no_pyrex_in_base_lattice():
    pm = AssemblyPinMapPatchItem(
        lattice_size=(3, 3),
        default_universe_id="fuel",
        guide_tube_coords=[(1, 1)],
        localized_insert_intents=[
            LocalizedInsertIntentPatchItem(
                insert_id="pyrex1",
                insert_kind="pyrex_rod",
                insert_universe_id="pyrex",
                coordinates=[(1, 1)],
            ),
        ],
    )
    pattern = _expand_assembly_pin_map(pm)
    for row in pattern:
        for cell in row:
            assert cell != "pyrex", "Pyrex found in base lattice!"


def test_no_thimble_in_base_lattice():
    pm = AssemblyPinMapPatchItem(
        lattice_size=(3, 3),
        default_universe_id="fuel",
        guide_tube_coords=[(0, 0), (2, 2)],
        localized_insert_intents=[
            LocalizedInsertIntentPatchItem(
                insert_id="th1",
                insert_kind="thimble_plug",
                insert_universe_id="thimble",
                coordinates=[(0, 0)],
            ),
        ],
    )
    pattern = _expand_assembly_pin_map(pm)
    for row in pattern:
        for cell in row:
            assert cell != "thimble", "Thimble found in base lattice!"


def test_no_absorber_in_base_lattice():
    pm = AssemblyPinMapPatchItem(
        lattice_size=(3, 3),
        default_universe_id="fuel",
        guide_tube_coords=[(1, 1)],
        localized_insert_intents=[
            LocalizedInsertIntentPatchItem(
                insert_id="abs1",
                insert_kind="absorber_insert",
                insert_universe_id="absorber",
                coordinates=[(1, 1)],
            ),
        ],
    )
    pattern = _expand_assembly_pin_map(pm)
    for row in pattern:
        for cell in row:
            assert cell != "absorber", "Absorber found in base lattice!"


def test_no_control_rod_in_base_lattice():
    pm = AssemblyPinMapPatchItem(
        lattice_size=(3, 3),
        default_universe_id="fuel",
        guide_tube_coords=[(1, 1)],
        localized_insert_intents=[
            LocalizedInsertIntentPatchItem(
                insert_id="rcca1",
                insert_kind="control_rod",
                insert_universe_id="rcca",
                coordinates=[(1, 1)],
            ),
        ],
    )
    pattern = _expand_assembly_pin_map(pm)
    for row in pattern:
        for cell in row:
            assert cell != "rcca", "Control rod found in base lattice!"


def test_base_lattice_only_persistent_paths():
    """Base lattice should only have default + guide_tube + instrument_tube + water."""
    pm = AssemblyPinMapPatchItem(
        lattice_size=(3, 3),
        default_universe_id="fuel",
        guide_tube_coords=[(1, 1)],
        instrument_tube_coords=[(0, 0)],
        water_cell_coords=[(2, 2)],
    )
    pattern = _expand_assembly_pin_map(pm)
    all_cells = {cell for row in pattern for cell in row}
    assert all_cells == {"fuel"}


def test_guide_tube_positions_preserved_in_base():
    """Guide tube coords should still be accessible (just filled with default)."""
    pm = AssemblyPinMapPatchItem(
        lattice_size=(3, 3),
        default_universe_id="fuel",
        guide_tube_coords=[(1, 1)],
    )
    pattern = _expand_assembly_pin_map(pm)
    # All cells should be fuel (default)
    for row in pattern:
        for cell in row:
            assert cell == "fuel"


def test_base_lattice_report():
    """Hierarchical plan should report localized_inserts_in_base_lattice=False."""
    catalog = AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="type_a",
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(3, 3),
                    default_universe_id="fuel",
                    guide_tube_coords=[(1, 1)],
                    localized_insert_intents=[
                        LocalizedInsertIntentPatchItem(
                            insert_id="pyrex1",
                            insert_kind="pyrex_rod",
                            insert_universe_id="pyrex",
                            coordinates=[(1, 1)],
                        ),
                    ],
                ),
            ),
        ],
    )
    from openmc_agent.plan_builder.patches import CoreLayoutPatch
    layout = CoreLayoutPatch(
        shape=(1, 1),
        assembly_pattern=[["type_a"]],
        boundary="reflective",
    )
    result = build_hierarchical_core_plan(catalog, layout, facts=None)
    assert result.summary["localized_inserts_in_base_lattice"] is False
