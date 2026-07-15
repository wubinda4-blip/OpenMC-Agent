"""Tests for scope-aware count aggregation (P2-FULLCORE-1)."""

from openmc_agent.plan_builder.scoped_counts import (
    aggregate_core_counts,
    compute_assembly_pin_counts,
    AssemblyTypeCountSummary,
)


def test_compute_assembly_pin_counts_basic():
    """Local counts computed from sparse pin map, not from division."""
    summary = compute_assembly_pin_counts(
        lattice_size=(3, 3),
        guide_tube_coords=[(0, 0), (2, 2)],
        instrument_tube_coords=[(1, 1)],
        water_cell_coords=[],
    )
    assert summary.total_cells == 9
    assert summary.fuel_pin_count == 6  # 9 - 2 - 1
    assert summary.guide_tube_count == 2
    assert summary.instrument_tube_count == 1
    assert summary.water_cell_count == 0


def test_compute_assembly_pin_counts_with_inserts():
    summary = compute_assembly_pin_counts(
        lattice_size=(5, 5),
        guide_tube_coords=[(1, 1), (3, 3)],
        instrument_tube_coords=[(2, 2)],
        water_cell_coords=[],
        localized_insert_counts={"pyrex_rod": 1, "thimble_plug": 1},
    )
    assert summary.localized_insert_counts["pyrex_rod"] == 1
    assert summary.localized_insert_counts["thimble_plug"] == 1


def test_aggregate_homogeneous_core():
    """Homogeneous 2x2 core: all same type, counts ×4."""
    summaries = {
        "type_a": compute_assembly_pin_counts(
            lattice_size=(3, 3),
            guide_tube_coords=[(1, 1)],
            instrument_tube_coords=[],
            water_cell_coords=[],
        ),
    }
    multiplicities = {"type_a": 4}
    agg = aggregate_core_counts(summaries, multiplicities)
    assert agg.total_assembly_instances == 4
    assert agg.core_total_for_role("fuel_pin") == 4 * 8  # 8 fuel per assembly
    assert agg.core_total_for_role("guide_tube") == 4 * 1


def test_aggregate_heterogeneous_core():
    """Heterogeneous 2x2 core: A and B with different pin maps."""
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
    assert agg.total_assembly_instances == 4
    # type_a: 8 fuel × 2 = 16
    # type_b: 6 fuel × 2 = 12
    assert agg.core_total_for_role("fuel_pin") == 28
    # type_a: 1 gt × 2 = 2
    # type_b: 2 gt × 2 = 4
    assert agg.core_total_for_role("guide_tube") == 6


def test_aggregate_localized_inserts():
    """Localized insert counts aggregated separately by insert kind."""
    summaries = {
        "type_a": AssemblyTypeCountSummary(
            assembly_type_id="type_a",
            lattice_size=(3, 3),
            total_cells=9,
            fuel_pin_count=6,
            guide_tube_count=2,
            instrument_tube_count=1,
            water_cell_count=0,
            localized_insert_counts={"pyrex_rod": 2, "thimble_plug": 1},
        ),
    }
    multiplicities = {"type_a": 3}
    agg = aggregate_core_counts(summaries, multiplicities)
    assert agg.core_total_for_role("localized_pyrex_rod") == 6
    assert agg.core_total_for_role("localized_thimble_plug") == 3
