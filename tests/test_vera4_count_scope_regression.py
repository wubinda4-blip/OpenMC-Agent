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
    resolve_expected_counts_for_pin_map,
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


# ---------------------------------------------------------------------------
# resolve_expected_counts_for_pin_map — the per-assembly scope resolver that
# prevents core_total counts from being compared against a per-assembly
# pin_map (the VERA4 false-mismatch: 264 vs 2376, etc.).
# ---------------------------------------------------------------------------


def _vera4_scoped_counts():
    """Faithful subset of the VERA4 facts scoped_expected_counts."""
    core_total = [
        ("fuel_pin", 2376), ("guide_tube", 216), ("instrument_tube", 9),
        ("pyrex", 80), ("thimble_plug", 112),
    ]
    by_type = [
        ("fuel_pin", "C", 264), ("fuel_pin", "E", 264), ("fuel_pin", "R", 264),
        ("guide_tube", "C", 24), ("guide_tube", "E", 24), ("guide_tube", "R", 24),
        ("instrument_tube", "C", 1), ("instrument_tube", "E", 1), ("instrument_tube", "R", 1),
        ("pyrex", "E", 20),
        ("thimble_plug", "C", 24), ("thimble_plug", "E", 4),
    ]
    scoped = [{"role": r, "value": v, "scope": "core_total"} for r, v in core_total]
    scoped += [
        {"role": r, "value": v, "scope": "assembly_type", "assembly_type_id": t}
        for r, t, v in by_type
    ]
    return scoped


def test_resolve_vera4_per_assembly_counts():
    """VERA4 core_total scoped counts resolve to per-assembly pin_map counts.

    This is the exact regression: the validator previously compared the
    per-assembly pin_map (264/24/1/20/28) against core_total (2376/216/9/80/
    112). Resolved expected counts must match the per-assembly actuals.
    """
    resolved = resolve_expected_counts_for_pin_map(
        _vera4_scoped_counts(),
        model_scope="multi_assembly_core",
        assembly_count=9,
        assembly_type_counts={"C": 4, "E": 4, "R": 1},
    )
    assert resolved["fuel_pin"] == 264          # shared across C/E/R
    assert resolved["guide_tube"] == 24         # shared
    assert resolved["instrument_tube"] == 1     # shared
    assert resolved["pyrex_rod"] == 20          # pyrex -> pyrex_rod; E only -> 20
    assert resolved["thimble_plug"] == 28       # C=24 + E=4 superposed


def test_resolve_single_assembly_is_noop():
    """Single-assembly models keep the legacy path (resolver returns empty)."""
    scoped = [{"role": "fuel_pin", "value": 264, "scope": "core_total"}]
    resolved = resolve_expected_counts_for_pin_map(
        scoped, model_scope="single_assembly", assembly_count=1,
    )
    assert resolved == {}


def test_resolve_skips_indivisible_core_total_only_role():
    """A role with only a core_total value that isn't a clean per-assembly
    multiple (heterogeneous core) is skipped, not divided into a wrong value."""
    scoped = [{"role": "pyrex", "value": 80, "scope": "core_total"}]  # 80 / 9 not int
    resolved = resolve_expected_counts_for_pin_map(
        scoped,
        model_scope="multi_assembly_core",
        assembly_count=9,
        assembly_type_counts={"C": 4, "E": 4, "R": 1},
    )
    assert "pyrex_rod" not in resolved  # cannot prove homogeneous -> skip


def test_resolve_shared_vs_superposed_logic():
    """Shared-across-all-types roles take the common value; subset/differing
    roles sum (superposed positions)."""
    scoped = [
        {"role": "fuel_pin", "value": 200, "scope": "assembly_type", "assembly_type_id": "A"},
        {"role": "fuel_pin", "value": 200, "scope": "assembly_type", "assembly_type_id": "B"},
        {"role": "absorber", "value": 8, "scope": "assembly_type", "assembly_type_id": "A"},
        {"role": "absorber", "value": 3, "scope": "assembly_type", "assembly_type_id": "B"},
    ]
    resolved = resolve_expected_counts_for_pin_map(
        scoped, model_scope="full_core", assembly_count=4,
        assembly_type_counts={"A": 2, "B": 2},
    )
    assert resolved["fuel_pin"] == 200    # identical across types -> common value
    assert resolved["absorber"] == 11     # 8 + 3 -> superposed sum
