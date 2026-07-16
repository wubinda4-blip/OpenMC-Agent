"""Tests for hierarchical reachability report.

Verifies the full chain: axial layer → core lattice → assembly universe →
derived pin lattice → grid-decorated universe → frame cell →
square_frame region → grid material.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from synthetic_grid_fixture import build_synthetic_grid_plan

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from vera4_base_fixture import build_all_vera4_patches

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.grid_geometry_validation import (
    build_grid_geometry_reachability_report,
)


class TestSyntheticReachability:
    """Reachability on the synthetic fixture."""

    def test_reachability_passes(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        rep = build_grid_geometry_reachability_report(plan)
        assert rep.result == "pass"

    def test_active_overlay_ids(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        rep = build_grid_geometry_reachability_report(plan)
        assert len(rep.active_overlay_ids) > 0

    def test_decorated_universe_ids_present(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        rep = build_grid_geometry_reachability_report(plan)
        assert len(rep.decorated_universe_ids) > 0

    def test_frame_cell_ids_present(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        rep = build_grid_geometry_reachability_report(plan)
        assert len(rep.frame_cell_ids) > 0

    def test_frame_region_ids_present(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        rep = build_grid_geometry_reachability_report(plan)
        assert len(rep.frame_region_ids) > 0

    def test_grid_material_ids_present(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        rep = build_grid_geometry_reachability_report(plan)
        assert len(rep.grid_material_ids) > 0

    def test_no_missing_refs(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        rep = build_grid_geometry_reachability_report(plan)
        assert len(rep.missing_refs) == 0

    def test_no_unreachable_refs(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        rep = build_grid_geometry_reachability_report(plan)
        assert len(rep.unreachable_refs) == 0

    def test_no_grids_returns_pass(self):
        from synthetic_grid_fixture import build_synthetic_no_grid_plan
        plan = build_synthetic_no_grid_plan()
        rep = build_grid_geometry_reachability_report(plan)
        assert rep.result == "pass"
        assert len(rep.active_overlay_ids) == 0


class TestVERA4Reachability:
    """Reachability on the VERA4 fixture."""

    def test_reachability_passes(self):
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        rep = build_grid_geometry_reachability_report(result.plan)
        assert rep.result == "pass"

    def test_vera4_has_8_overlays(self):
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        rep = build_grid_geometry_reachability_report(result.plan)
        assert len(rep.active_overlay_ids) == 8

    def test_vera4_has_decorated_universes(self):
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        rep = build_grid_geometry_reachability_report(result.plan)
        assert len(rep.decorated_universe_ids) > 0

    def test_vera4_has_frame_cells(self):
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        rep = build_grid_geometry_reachability_report(result.plan)
        assert len(rep.frame_cell_ids) > 0

    def test_vera4_zero_missing(self):
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        rep = build_grid_geometry_reachability_report(result.plan)
        assert len(rep.missing_refs) == 0

    def test_vera4_zero_unreachable(self):
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        rep = build_grid_geometry_reachability_report(result.plan)
        assert len(rep.unreachable_refs) == 0

    def test_to_dict_roundtrip(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        rep = build_grid_geometry_reachability_report(plan)
        d = rep.to_dict()
        assert "result" in d
        assert "active_overlay_ids" in d
        assert "decorated_universe_ids" in d
        import json
        json.dumps(d)
