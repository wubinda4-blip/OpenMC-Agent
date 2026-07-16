"""Tests for grid-on / grid-off structural comparison.

Verifies that adding spacer grids changes the geometry IR: decorated
universes appear, frame cells exist, grid materials become reachable,
and the structural digest differs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from synthetic_grid_fixture import build_synthetic_grid_plan, build_synthetic_no_grid_plan

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from vera4_base_fixture import build_all_vera4_patches

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.grid_geometry_validation import (
    compute_geometry_structural_digest,
)


class TestSyntheticGridOnOff:
    """Grid-on vs grid-off on the synthetic fixture."""

    def test_digest_differs(self):
        plan_on = build_synthetic_grid_plan(grid_on=True)
        plan_off = build_synthetic_no_grid_plan()
        d_on = compute_geometry_structural_digest(plan_on.complex_model)
        d_off = compute_geometry_structural_digest(plan_off.complex_model)
        assert d_on != d_off

    def test_grid_on_has_decorated_universes(self):
        plan_on = build_synthetic_grid_plan(grid_on=True)
        decorated = [u for u in plan_on.complex_model.universes if "__grid__" in u.id]
        assert len(decorated) > 0

    def test_grid_off_no_decorated_universes(self):
        plan_off = build_synthetic_no_grid_plan()
        decorated = [u for u in plan_off.complex_model.universes if "__grid__" in u.id]
        assert len(decorated) == 0

    def test_grid_on_has_frame_cells(self):
        plan_on = build_synthetic_grid_plan(grid_on=True)
        cm = plan_on.complex_model
        frame_cells = [
            c for c in cm.cells
            if "grid_frame" in (c.component_role or "").lower()
            or "grid_frame" in c.id.lower()
        ]
        assert len(frame_cells) > 0

    def test_grid_off_no_frame_cells(self):
        plan_off = build_synthetic_no_grid_plan()
        cm = plan_off.complex_model
        frame_cells = [
            c for c in cm.cells
            if "grid_frame" in (c.component_role or "").lower()
            or "grid_frame" in c.id.lower()
        ]
        assert len(frame_cells) == 0

    def test_grid_on_has_grid_material_reachable(self):
        plan_on = build_synthetic_grid_plan(grid_on=True)
        cm = plan_on.complex_model
        # Find frame cells and check they reference grid material
        frame_cells = [
            c for c in cm.cells
            if "grid_frame" in (c.component_role or "").lower()
        ]
        frame_mats = {c.fill_id for c in frame_cells if c.fill_type == "material" and c.fill_id}
        assert "grid_end_mat" in frame_mats

    def test_grid_off_no_grid_material_reachable(self):
        plan_off = build_synthetic_no_grid_plan()
        cm = plan_off.complex_model
        all_cell_mats = {
            c.fill_id for c in cm.cells
            if c.fill_type == "material"
        }
        assert "grid_end_mat" not in all_cell_mats
        assert "grid_mid_mat" not in all_cell_mats

    def test_universe_count_differs(self):
        plan_on = build_synthetic_grid_plan(grid_on=True)
        plan_off = build_synthetic_no_grid_plan()
        assert len(plan_on.complex_model.universes) > len(plan_off.complex_model.universes)

    def test_cell_count_differs(self):
        plan_on = build_synthetic_grid_plan(grid_on=True)
        plan_off = build_synthetic_no_grid_plan()
        assert len(plan_on.complex_model.cells) > len(plan_off.complex_model.cells)

    def test_surface_count_differs(self):
        plan_on = build_synthetic_grid_plan(grid_on=True)
        plan_off = build_synthetic_no_grid_plan()
        assert len(plan_on.complex_model.surfaces) > len(plan_off.complex_model.surfaces)


class TestVERA4GridOnOff:
    """Grid-on vs grid-off on VERA4 (remove overlays only)."""

    def test_vera4_digest_differs(self):
        patches_on = build_all_vera4_patches()
        patches_off = [p for p in patches_on if p.patch_type != "axial_overlays"]
        result_on = assemble_simulation_plan_from_patches(patches_on, strict=False)
        result_off = assemble_simulation_plan_from_patches(patches_off, strict=False)
        d_on = compute_geometry_structural_digest(result_on.plan.complex_model)
        d_off = compute_geometry_structural_digest(result_off.plan.complex_model)
        assert d_on != d_off

    def test_vera4_grid_on_has_decorated(self):
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        decorated = [u for u in result.plan.complex_model.universes if "__grid__" in u.id]
        assert len(decorated) > 0

    def test_vera4_grid_off_no_decorated(self):
        patches = build_all_vera4_patches()
        patches_off = [p for p in patches if p.patch_type != "axial_overlays"]
        result = assemble_simulation_plan_from_patches(patches_off, strict=False)
        decorated = [u for u in result.plan.complex_model.universes if "__grid__" in u.id]
        assert len(decorated) == 0
