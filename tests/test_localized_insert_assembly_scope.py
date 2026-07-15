"""Tests for localized insert assembly scope binding (P2-FULLCORE-1).

Verifies that localized inserts are scoped to their assembly type and
not applied to other types or all core positions.
"""

from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    LocalizedInsertIntentPatchItem,
)
from openmc_agent.plan_builder.hierarchical_assembler import (
    assemble_assembly_templates,
)
from openmc_agent.plan_builder.scoped_counts import aggregate_core_counts


def test_inserts_scoped_to_type():
    """Each assembly type has its own localized inserts."""
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
                            insert_id="pyrex_a",
                            insert_kind="pyrex_rod",
                            insert_universe_id="pyrex",
                            coordinates=[(1, 1)],
                        ),
                    ],
                ),
            ),
            AssemblyTypePatchItem(
                assembly_type_id="type_b",
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(3, 3),
                    default_universe_id="fuel",
                    guide_tube_coords=[(0, 0), (2, 2)],
                    # type_b has NO localized inserts
                ),
            ),
        ],
    )
    _, _, _, _, _, summaries, _, _, _ = assemble_assembly_templates(catalog)
    assert summaries["type_a"].localized_insert_counts.get("pyrex_rod") == 1
    assert "pyrex_rod" not in summaries["type_b"].localized_insert_counts


def test_insert_aggregation_respects_type():
    """Core-level insert counts = Σ multiplicity[type] × local_inserts[type]."""
    summaries_dict = {
        "type_a": assemble_assembly_templates(
            AssemblyCatalogPatch(
                assembly_types=[
                    AssemblyTypePatchItem(
                        assembly_type_id="type_a",
                        pin_map=AssemblyPinMapPatchItem(
                            lattice_size=(3, 3),
                            default_universe_id="fuel",
                            guide_tube_coords=[(1, 1)],
                            localized_insert_intents=[
                                LocalizedInsertIntentPatchItem(
                                    insert_id="p1",
                                    insert_kind="pyrex_rod",
                                    insert_universe_id="p",
                                    coordinates=[(1, 1)],
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        )[5]["type_a"],
        "type_b": assemble_assembly_templates(
            AssemblyCatalogPatch(
                assembly_types=[
                    AssemblyTypePatchItem(
                        assembly_type_id="type_b",
                        pin_map=AssemblyPinMapPatchItem(
                            lattice_size=(3, 3),
                            default_universe_id="fuel",
                            guide_tube_coords=[(0, 0), (2, 2)],
                            localized_insert_intents=[
                                LocalizedInsertIntentPatchItem(
                                    insert_id="p1",
                                    insert_kind="pyrex_rod",
                                    insert_universe_id="p",
                                    coordinates=[(0, 0), (2, 2)],
                                ),
                                LocalizedInsertIntentPatchItem(
                                    insert_id="t1",
                                    insert_kind="thimble_plug",
                                    insert_universe_id="t",
                                    coordinates=[(0, 0)],
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        )[5]["type_b"],
    }
    multiplicities = {"type_a": 2, "type_b": 2}
    agg = aggregate_core_counts(summaries_dict, multiplicities)
    # pyrex: type_a 1×2 + type_b 2×2 = 6
    assert agg.core_total_for_role("localized_pyrex_rod") == 6
    # thimble: only type_b 1×2 = 2
    assert agg.core_total_for_role("localized_thimble_plug") == 2


def test_different_types_different_insert_counts():
    """Verify different assembly types can have completely different inserts."""
    summaries = {
        "type_a": type("S", (), {
            "assembly_type_id": "type_a",
            "lattice_size": (3, 3),
            "total_cells": 9,
            "fuel_pin_count": 7,
            "guide_tube_count": 1,
            "instrument_tube_count": 1,
            "water_cell_count": 0,
            "localized_insert_counts": {"pyrex_rod": 1},
        })(),
        "type_b": type("S", (), {
            "assembly_type_id": "type_b",
            "lattice_size": (3, 3),
            "total_cells": 9,
            "fuel_pin_count": 6,
            "guide_tube_count": 2,
            "instrument_tube_count": 1,
            "water_cell_count": 0,
            "localized_insert_counts": {"thimble_plug": 2},
        })(),
    }
    multiplicities = {"type_a": 3, "type_b": 1}
    agg = aggregate_core_counts(summaries, multiplicities)
    assert agg.core_total_for_role("localized_pyrex_rod") == 3  # 1×3
    assert agg.core_total_for_role("localized_thimble_plug") == 2  # 2×1
