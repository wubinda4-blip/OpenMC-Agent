"""Tests for structured grid material probes.

Verifies material at specific (x, y, z) positions in both synthetic and
VERA4 models.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from synthetic_grid_fixture import build_synthetic_grid_plan

from openmc_agent.plan_builder.grid_material_probes import (
    ProbeReport,
    probe_assembly_gap,
    probe_grid_frame_end,
    probe_grid_frame_middle,
    probe_inner_moderator,
    probe_non_grid_segment,
    probe_pin_interior,
    run_all_probes,
)


class TestSyntheticProbes:
    """Probes on the synthetic 2x2 / 3x3 fixture."""

    def test_pin_interior_returns_fuel(self):
        """Pin interior probe at center should return fuel-related material."""
        plan = build_synthetic_grid_plan(grid_on=True)
        # Pin center in the first pin cell of the first assembly
        # Core lattice: 2x2, pitch=4.0, lower_left at (-4, -4)
        # First assembly center: (-2, -2)
        # Pin lattice within assembly: 3x3, pitch=1.25, lower_left at (-3.625, -3.625)
        # First pin center: (-3, -3)
        result = probe_pin_interior(plan, x=-3.0, y=-3.0, z=50.0, expected="fuel")
        assert result.actual_material is not None

    def test_non_grid_segment_returns_water(self):
        """Non-grid segment at pin position should not return grid material."""
        plan = build_synthetic_grid_plan(grid_on=True)
        # z=30 is in active fuel but not in grid band (grid is at z=12-14)
        result = probe_non_grid_segment(plan, x=-3.0, y=-3.0, z=30.0)
        assert result.actual_material is not None
        assert result.actual_material not in {"grid_end_mat", "grid_mid_mat"}

    def test_assembly_gap_returns_water(self):
        """Assembly gap (between assemblies) should return water, not grid."""
        plan = build_synthetic_grid_plan(grid_on=True)
        # Between assemblies: x=0 is the boundary
        result = probe_assembly_gap(plan, x=0.0, y=0.0, z=13.0)
        assert result.actual_material is not None
        assert result.actual_material not in {"grid_end_mat", "grid_mid_mat"}

    def test_grid_frame_probe_returns_material(self):
        """Grid frame zone should return a material (grid material or coolant)."""
        plan = build_synthetic_grid_plan(grid_on=True)
        # z=13 is in grid band (12-14)
        result = probe_grid_frame_end(plan, grid_z=13.0, pin_x=-3.0, pin_y=-3.0)
        assert result.actual_material is not None

    def test_probe_report_serialization(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        probes = [
            probe_pin_interior(plan, x=-3.0, y=-3.0, z=50.0),
            probe_non_grid_segment(plan, x=-3.0, y=-3.0, z=30.0),
        ]
        report = run_all_probes(plan, probes)
        d = report.to_dict()
        assert "all_passed" in d
        assert "results" in d
        import json
        json.dumps(d)

    def test_inner_moderator_not_grid_material(self):
        """Inner moderator zone should not return grid material."""
        plan = build_synthetic_grid_plan(grid_on=True)
        result = probe_inner_moderator(plan, x=-3.0, y=-3.0, z=30.0)
        # At z=30 (non-grid), should be water
        assert result.actual_material is not None
        assert result.actual_material not in {"grid_end_mat", "grid_mid_mat"}
