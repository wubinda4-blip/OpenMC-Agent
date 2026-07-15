"""Tests proving heterogeneous cores forbid division-based count derivation (P2-FULLCORE-1)."""

import pytest

from openmc_agent.plan_builder.scoped_counts import (
    aggregate_core_counts,
    compute_assembly_pin_counts,
    derive_homogeneous_local_counts_if_proven,
)


def test_heterogeneous_core_total_not_divisible():
    """In a heterogeneous core, total/count does not give per-type counts."""
    # type_a: 8 fuel pins, type_b: 6 fuel pins
    # total = 8*2 + 6*2 = 28
    # 28 / 4 = 7 → WRONG for both types
    summaries = {
        "type_a": compute_assembly_pin_counts(
            lattice_size=(3, 3),
            guide_tube_coords=[(1, 1)],
            instrument_tube_coords=[],
            water_cell_coords=[],
        ),
        "type_b": compute_assembly_pin_counts(
            lattice_size=(3, 3),
            guide_tube_coords=[(0, 0), (2, 2)],
            instrument_tube_coords=[(1, 1)],
            water_cell_coords=[],
        ),
    }
    multiplicities = {"type_a": 2, "type_b": 2}
    agg = aggregate_core_counts(summaries, multiplicities)
    total_fuel = agg.core_total_for_role("fuel_pin")
    assert total_fuel == 28
    # Division would give 7, which is wrong for both types
    assert total_fuel // 4 != summaries["type_a"].fuel_pin_count
    assert total_fuel // 4 != summaries["type_b"].fuel_pin_count


def test_division_fails_when_types_have_different_guide_tubes():
    """Different guide tube counts prevent any uniform division."""
    summaries = {
        "type_a": compute_assembly_pin_counts(
            lattice_size=(5, 5),
            guide_tube_coords=[(1, 1)],
            instrument_tube_coords=[],
            water_cell_coords=[],
        ),
        "type_b": compute_assembly_pin_counts(
            lattice_size=(5, 5),
            guide_tube_coords=[(0, 0), (4, 4), (2, 2)],
            instrument_tube_coords=[(1, 1)],
            water_cell_coords=[],
        ),
    }
    multiplicities = {"type_a": 2, "type_b": 2}
    agg = aggregate_core_counts(summaries, multiplicities)
    total_gt = agg.core_total_for_role("guide_tube")
    # type_a: 1 gt × 2 = 2, type_b: 3 gt × 2 = 6 → total = 8
    assert total_gt == 8
    # 8 / 4 = 2, which is wrong for both types
    assert total_gt // 4 != summaries["type_a"].guide_tube_count
    assert total_gt // 4 != summaries["type_b"].guide_tube_count


def test_derivation_rejected_for_heterogeneous():
    """Homogeneous derivation is rejected when types differ."""
    per_assembly, note = derive_homogeneous_local_counts_if_proven(
        core_total=28,
        assembly_count=4,
        assembly_type_count=2,
        input_states_homogeneous=False,
        input_states_identical=False,
    )
    assert per_assembly is None
    assert "identical" in note.lower() or "not" in note.lower()


def test_localized_inserts_not_uniformly_divisible():
    """Localized insert counts vary by type, cannot divide."""
    summaries = {
        "type_a": compute_assembly_pin_counts(
            lattice_size=(3, 3),
            guide_tube_coords=[(1, 1)],
            instrument_tube_coords=[],
            water_cell_coords=[],
            localized_insert_counts={"pyrex_rod": 1},
        ),
        "type_b": compute_assembly_pin_counts(
            lattice_size=(3, 3),
            guide_tube_coords=[(0, 0), (2, 2)],
            instrument_tube_coords=[(1, 1)],
            water_cell_coords=[],
            localized_insert_counts={"pyrex_rod": 2, "thimble_plug": 1},
        ),
    }
    multiplicities = {"type_a": 2, "type_b": 2}
    agg = aggregate_core_counts(summaries, multiplicities)
    total_pyrex = agg.core_total_for_role("localized_pyrex_rod")
    # type_a: 1 × 2 = 2, type_b: 2 × 2 = 4 → total = 6
    assert total_pyrex == 6
    # 6 / 4 = 1.5 → not even an integer
    assert total_pyrex % 4 != 0
