"""Tests for VERA3 3B finite insert profiles (Pyrex and thimble).

Covers:
- Pyrex 16 coordinates correct
- thimble 8 coordinates correct
- Pyrex/thimble coordinates do not overlap
- guide outer radius = 0.602
- instrument outer radius = 0.605
- thimble plug radius = 0.538
- upper plenum three segments continuous
- middle plenum layer composes two loadings
- top spacer overlay independent
- 3B base lattice still 24 guide tubes
- Pyrex no longer uses legacy override
- Pyrex/thimble inner profiles don't contain guide wall
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches

FIXTURE_PATH = Path("tests/fixtures/vera3_patches/vera3_3b_patches.json")


@pytest.fixture
def assembled_3b():
    with open(FIXTURE_PATH) as f:
        data = json.load(f)
    patches = [parse_patch_content(p["patch_type"], p) for p in data["patches"]]
    return assemble_simulation_plan_from_patches(patches)


class TestPyrexCoordinates:
    def test_pyrex_16_coordinates(self, assembled_3b):
        loading = next(l for l in assembled_3b.plan.complex_model.lattice_loadings
                       if l.id == "pyrex_active_loading")
        ops = [t for t in loading.transformations if t.operation_kind == "nested_component_override"]
        assert len(ops) == 1
        assert len(ops[0].target_coordinates) == 16

    def test_pyrex_not_legacy_override(self, assembled_3b):
        loading = next(l for l in assembled_3b.plan.complex_model.lattice_loadings
                       if l.id == "pyrex_active_loading")
        # Must not have legacy overrides
        assert not loading.overrides

    def test_pyrex_operation_kind_nested(self, assembled_3b):
        loading = next(l for l in assembled_3b.plan.complex_model.lattice_loadings
                       if l.id == "pyrex_active_loading")
        kinds = {t.operation_kind for t in loading.transformations}
        assert "nested_component_override" in kinds


class TestThimbleCoordinates:
    def test_thimble_8_coordinates(self, assembled_3b):
        loading = next(l for l in assembled_3b.plan.complex_model.lattice_loadings
                       if l.id == "thimble_plug_loading")
        ops = [t for t in loading.transformations if t.operation_kind == "nested_component_override"]
        assert len(ops) == 1
        assert len(ops[0].target_coordinates) == 8

    def test_pyrex_thimble_no_overlap(self, assembled_3b):
        pyrex = next(l for l in assembled_3b.plan.complex_model.lattice_loadings
                     if l.id == "pyrex_active_loading")
        thimble = next(l for l in assembled_3b.plan.complex_model.lattice_loadings
                       if l.id == "thimble_plug_loading")
        pyrex_op = next(t for t in pyrex.transformations if t.operation_kind == "nested_component_override")
        thimble_op = next(t for t in thimble.transformations if t.operation_kind == "nested_component_override")
        pyrex_set = set(tuple(c) for c in pyrex_op.target_coordinates)
        thimble_set = set(tuple(c) for c in thimble_op.target_coordinates)
        assert pyrex_set & thimble_set == set()


class TestTubeRadii:
    def test_guide_outer_radius_0602(self, assembled_3b):
        model = assembled_3b.plan.complex_model
        for u in model.universes:
            if "guide" not in u.id.lower():
                continue
            u_cells = [c for c in model.cells if c.id in u.cell_ids]
            for cell in u_cells:
                if cell.component_role != "tube_wall":
                    continue
                region = next((r for r in model.regions if r.id == cell.region_id), None)
                assert region is not None
                max_r = 0.0
                for sid in region.surface_ids:
                    surf = next((s for s in model.surfaces if s.id == sid), None)
                    if surf and surf.kind == "zcylinder":
                        r = surf.parameters.get("r", 0)
                        max_r = max(max_r, r)
                assert abs(max_r - 0.602) < 1e-6, f"guide tube outer radius {max_r} != 0.602"

    def test_instrument_outer_radius_0605(self, assembled_3b):
        model = assembled_3b.plan.complex_model
        for u in model.universes:
            if "instrument" not in u.id.lower():
                continue
            u_cells = [c for c in model.cells if c.id in u.cell_ids]
            for cell in u_cells:
                if cell.component_role != "tube_wall":
                    continue
                region = next((r for r in model.regions if r.id == cell.region_id), None)
                assert region is not None
                max_r = 0.0
                for sid in region.surface_ids:
                    surf = next((s for s in model.surfaces if s.id == sid), None)
                    if surf and surf.kind == "zcylinder":
                        r = surf.parameters.get("r", 0)
                        max_r = max(max_r, r)
                assert abs(max_r - 0.605) < 1e-6, f"instrument tube outer radius {max_r} != 0.605"

    def test_thimble_plug_radius_0538(self, assembled_3b):
        model = assembled_3b.plan.complex_model
        thimble_u = next((u for u in model.universes if "thimble_inner" in u.id), None)
        assert thimble_u is not None
        plug_cell = next(
            (c for c in model.cells if c.id in thimble_u.cell_ids and c.component_role == "plug"),
            None,
        )
        assert plug_cell is not None
        region = next((r for r in model.regions if r.id == plug_cell.region_id), None)
        assert region is not None
        max_r = 0.0
        for sid in region.surface_ids:
            surf = next((s for s in model.surfaces if s.id == sid), None)
            if surf and surf.kind == "zcylinder":
                max_r = max(max_r, surf.parameters.get("r", 0))
        assert abs(max_r - 0.538) < 1e-6, f"thimble plug radius {max_r} != 0.538"


class TestUpperPlenum:
    def test_upper_plenum_three_segments(self, assembled_3b):
        layers = assembled_3b.plan.complex_model.core.axial_layers
        plenum_layers = [l for l in layers if "upper_plenum" in l.id]
        assert len(plenum_layers) == 3

    def test_upper_plenum_continuous(self, assembled_3b):
        layers = assembled_3b.plan.complex_model.core.axial_layers
        plenum_layers = sorted(
            (l for l in layers if "upper_plenum" in l.id),
            key=lambda l: l.z_min_cm,
        )
        assert abs(plenum_layers[0].z_min_cm - 379.381) < 1e-3
        assert abs(plenum_layers[-1].z_max_cm - 395.381) < 1e-3
        for prev, curr in zip(plenum_layers, plenum_layers[1:]):
            assert abs(curr.z_min_cm - prev.z_max_cm) < 1e-6

    def test_middle_plenum_layer_multi_loading(self, assembled_3b):
        layers = assembled_3b.plan.complex_model.core.axial_layers
        middle = next((l for l in layers if "middle" in l.id), None)
        assert middle is not None
        assert len(middle.loading_ids) == 3
        assert "plenum_loading" in middle.loading_ids
        assert "pyrex_upper_gas_loading" in middle.loading_ids
        assert any("thimble" in lid for lid in middle.loading_ids)

    def test_top_spacer_overlay_independent(self, assembled_3b):
        overlays = assembled_3b.plan.complex_model.core.axial_overlays
        top_grid = next((o for o in overlays if "end_top" in o.id or "7" in o.id), None)
        assert top_grid is not None
        assert top_grid.overlay_kind == "spacer_grid"


class TestBaseLattice:
    def test_base_lattice_24_guide_tubes(self, assembled_3b):
        lattice = assembled_3b.plan.complex_model.lattices[0]
        flat = [uid for row in lattice.universe_pattern for uid in row]
        assert sum(1 for u in flat if u == "guide_tube") == 24
        assert sum(1 for u in flat if u == "fuel_pin") == 264
        assert sum(1 for u in flat if u == "instrument_tube") == 1

    def test_base_lattice_no_finite_inserts(self, assembled_3b):
        lattice = assembled_3b.plan.complex_model.lattices[0]
        flat = [uid for row in lattice.universe_pattern for uid in row]
        assert "pyrex_rod" not in flat
        assert "pyrex_inner_profile" not in flat
        assert "thimble_plug" not in flat
        assert "thimble_inner_profile" not in flat


class TestInnerProfiles:
    def test_pyrex_inner_profile_no_guide_wall(self, assembled_3b):
        model = assembled_3b.plan.complex_model
        pyrex_u = next((u for u in model.universes if "pyrex_inner" in u.id), None)
        assert pyrex_u is not None
        u_cells = [c for c in model.cells if c.id in pyrex_u.cell_ids]
        # No cell should have tube_wall role
        assert not any(c.component_role == "tube_wall" for c in u_cells)

    def test_thimble_inner_profile_no_guide_wall(self, assembled_3b):
        model = assembled_3b.plan.complex_model
        thimble_u = next((u for u in model.universes if "thimble_inner" in u.id), None)
        assert thimble_u is not None
        u_cells = [c for c in model.cells if c.id in thimble_u.cell_ids]
        assert not any(c.component_role == "tube_wall" for c in u_cells)

    def test_pyrex_gas_gaps_are_helium(self, assembled_3b):
        model = assembled_3b.plan.complex_model
        pyrex_u = next((u for u in model.universes if "pyrex_inner" in u.id), None)
        assert pyrex_u is not None
        u_cells = [c for c in model.cells if c.id in pyrex_u.cell_ids]
        gas_gaps = [c for c in u_cells if c.component_role == "gas_gap"]
        assert len(gas_gaps) >= 2
        for gap in gas_gaps:
            assert gap.fill_id == "helium"
