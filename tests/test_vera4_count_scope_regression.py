"""Tests for VERA4 count scope regression (P2-FULLCORE-1).

Verifies that the original VERA4 failure mode (pin_map count mismatch caused
by comparing core-total counts with per-assembly pin maps) no longer occurs.

The original failure: fuel_pin 264 vs 2376, instrument_tube 1 vs 9, etc.
This was caused by validating core-total counts against a single pin_map.
"""

from openmc_agent.plan_builder.patches import FactsPatch, ScopedExpectedCount
from openmc_agent.plan_builder.scoped_counts import (
    validate_count_scope_compatibility,
    compare_scoped_expected_counts,
    normalize_scoped_counts,
    compute_assembly_pin_counts,
    aggregate_core_counts,
)


def test_core_total_not_compared_to_pin_map():
    """A core_total count should NOT be compared to pin_map actual counts."""
    # Core total: 2376 fuel pins
    # Pin map actual: 264 fuel pins (per assembly)
    expected = [
        ScopedExpectedCount(role="fuel_pin", value=2376, scope="core_total"),
    ]
    actual_pin_map = {"fuel_pin": 264}
    # Compare at pin_map scope → no match attempted for core_total entry
    result = compare_scoped_expected_counts(expected, actual_pin_map, scope="pin_map")
    assert result.ok  # No error because scopes differ


def test_instrument_tube_scope_mismatch():
    """1 instrument tube per assembly vs 9 total → should not mismatch at different scopes."""
    expected = [
        ScopedExpectedCount(role="instrument_tube", value=9, scope="core_total"),
    ]
    actual_pin_map = {"instrument_tube": 1}
    result = compare_scoped_expected_counts(expected, actual_pin_map, scope="pin_map")
    assert result.ok  # Different scope → not compared


def test_multi_assembly_model_scope_detected():
    """FactsPatch with multi_assembly_core should trigger scope validation."""
    facts = FactsPatch(
        model_scope="multi_assembly_core",
        assembly_count=9,
        expected_pin_count=264,  # ambiguous
    )
    counts = normalize_scoped_counts(facts)
    result = validate_count_scope_compatibility(facts, counts)
    assert not result.ok  # Should flag as ambiguous


def test_proper_scoped_counts_no_mismatch():
    """With proper scoped counts, no mismatch should occur."""
    # 9 assemblies, 264 fuel pins each
    summaries = {
        "type_a": compute_assembly_pin_counts(
            lattice_size=(17, 17),
            guide_tube_coords=[(i, j) for i in range(17) for j in range(17) if (i + j) % 2 == 0][:24],
            instrument_tube_coords=[(8, 8)],
            water_cell_coords=[],
        ),
    }
    multiplicities = {"type_a": 9}
    agg = aggregate_core_counts(summaries, multiplicities)

    # Compare core_total expected vs core_total actual
    expected = [
        ScopedExpectedCount(
            role="fuel_pin",
            value=264 * 9,
            scope="core_total",
        ),
    ]
    actual_core = {role: agg.core_total_for_role(role) for role in ["fuel_pin"]}
    result = compare_scoped_expected_counts(expected, actual_core, scope="core_total")
    assert result.ok


def test_heterogeneous_core_proper_aggregation():
    """Heterogeneous core: each type has different counts, no division."""
    summaries = {
        "corner": compute_assembly_pin_counts(
            lattice_size=(17, 17),
            guide_tube_coords=[(0, 0)],
            instrument_tube_coords=[],
            water_cell_coords=[],
        ),
        "edge": compute_assembly_pin_counts(
            lattice_size=(17, 17),
            guide_tube_coords=[(0, 0), (16, 0)],
            instrument_tube_coords=[],
            water_cell_coords=[],
        ),
        "center": compute_assembly_pin_counts(
            lattice_size=(17, 17),
            guide_tube_coords=[(0, 0), (16, 0), (0, 16), (16, 16)],
            instrument_tube_coords=[(8, 8)],
            water_cell_coords=[],
        ),
    }
    multiplicities = {"corner": 4, "edge": 4, "center": 1}
    agg = aggregate_core_counts(summaries, multiplicities)
    # Each type has different local fuel counts
    assert summaries["corner"].fuel_pin_count != summaries["center"].fuel_pin_count
    # Core total is the sum, not division
    expected_total = (
        summaries["corner"].fuel_pin_count * 4
        + summaries["edge"].fuel_pin_count * 4
        + summaries["center"].fuel_pin_count * 1
    )
    assert agg.core_total_for_role("fuel_pin") == expected_total
