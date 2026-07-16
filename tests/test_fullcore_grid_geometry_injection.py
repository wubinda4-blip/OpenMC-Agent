"""Tests for grid-decorated universe injection (P2-FULLCORE-2D-A-GRID-CLOSURE)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from vera4_base_fixture import build_all_vera4_patches
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.axial_state_materializer import (
    _make_grid_decorated_universe,
    _compute_grid_frame_exact,
)
from openmc_agent.plan_builder.patches import (
    CellLayerPatch,
    UniverseSpecPatch,
)


class TestGridDecoratedUniverseGeneration:
    def test_decorated_universe_has_frame_cell(self):
        base = UniverseSpecPatch(
            universe_id="fuel_pin", kind="fuel_pin",
            cells=[
                CellLayerPatch(id="pellet", role="fuel", material_id="fuel",
                               region_kind="cylinder", r_min_cm=0.0, r_max_cm=0.4),
                CellLayerPatch(id="clad", role="cladding", material_id="zircaloy4",
                               region_kind="cylinder", r_min_cm=0.4, r_max_cm=0.48),
                CellLayerPatch(id="coolant", role="coolant", material_id="water",
                               region_kind="background"),
            ],
        )
        decorated = _make_grid_decorated_universe(
            "fuel_pin", "abc123", "inconel718", 1.20, 1.26, base,
        )
        assert decorated is not None
        assert "__grid__" in decorated.universe_id
        roles = [c.role for c in decorated.cells]
        assert "grid_frame" in roles

    def test_decorated_preserves_cylinder_cells(self):
        base = UniverseSpecPatch(
            universe_id="gt", kind="guide_tube",
            cells=[
                CellLayerPatch(id="inner", role="inner_flow", material_id="water",
                               region_kind="cylinder", r_min_cm=0.0, r_max_cm=0.56),
                CellLayerPatch(id="wall", role="cladding", material_id="zircaloy4",
                               region_kind="cylinder", r_min_cm=0.56, r_max_cm=0.61),
                CellLayerPatch(id="coolant", role="coolant", material_id="water",
                               region_kind="background"),
            ],
        )
        decorated = _make_grid_decorated_universe(
            "gt", "def456", "zircaloy4", 1.20, 1.26, base,
        )
        assert decorated is not None
        # Original cells preserved
        inner = next(c for c in decorated.cells if c.role == "inner_flow")
        assert inner.r_max_cm == 0.56
        wall = next(c for c in decorated.cells if c.role == "cladding")
        assert wall.r_max_cm == 0.61


class TestGridInjectionInPlan:
    def test_grid_decorated_universes_exist(self):
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        assert result.ok
        grid_uvs = [u for u in result.plan.complex_model.universes if "__grid__" in u.id]
        assert len(grid_uvs) > 0, "No grid-decorated universes"

    def test_grid_decorated_in_lattice_patterns(self):
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        found = False
        for lat in result.plan.complex_model.lattices:
            if lat.universe_pattern:
                for row in lat.universe_pattern:
                    for uid in row:
                        if "__grid__" in uid:
                            found = True
        assert found, "No grid-decorated universe refs in lattice patterns"

    def test_grid_material_reachable(self):
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        mat_ids = {m.id for m in result.plan.complex_model.materials}
        assert "inconel718" in mat_ids

    def test_frame_surfaces_exist(self):
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        frame_surfs = [s for s in result.plan.complex_model.surfaces if "_frame_" in s.id]
        assert len(frame_surfs) > 0

    def test_grid_off_lack_decorated(self):
        """Without grid overlays, no decorated universes should appear."""
        patches = build_all_vera4_patches()
        # Remove grid overlays
        patches = [p for p in patches if p.patch_type != "axial_overlays"]
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        grid_uvs = [u for u in result.plan.complex_model.universes if "__grid__" in u.id]
        assert len(grid_uvs) == 0


class TestGridMassConservation:
    def test_inconel_end_grid_exact(self):
        a, inner, ft = _compute_grid_frame_exact(1017.0, 8.19, 289, 3.866, 1.26)
        from openmc_agent.plan_builder.axial_state_materializer import _back_calculate_mass
        back = _back_calculate_mass(inner, 1.26, 8.19, 289, 3.866)
        assert abs(back - 1017.0) / 1017.0 < 1e-6

    def test_zircaloy_middle_grid_exact(self):
        a, inner, ft = _compute_grid_frame_exact(875.0, 6.56, 289, 3.810, 1.26)
        from openmc_agent.plan_builder.axial_state_materializer import _back_calculate_mass
        back = _back_calculate_mass(inner, 1.26, 6.56, 289, 3.810)
        assert abs(back - 875.0) / 875.0 < 1e-6


class TestGridTransportSmoke:
    def test_vera4_with_grid_renders_and_exports(self):
        """The VERA4 model with grid injection should render and export."""
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        assert result.ok
        from openmc_agent.renderers.core import CoreRenderer
        renderer = CoreRenderer()
        cap = renderer.can_render(result.plan)
        assert cap.renderability in ("exportable", "runnable")
