"""VERA4-specific grid geometry acceptance tests.

Tests VERA4 quantities: 8 bands, 72 instances, 18 end grids, 54 middle grids,
Inconel/Zircaloy materials, frame cells, regions, reachability.
Also verifies VERA3 (no grid) regression.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from vera4_base_fixture import build_all_vera4_patches

from openmc_agent.campaign_eval.vera4_base_acceptance import (
    check_grid_geometry_level,
    run_full_acceptance,
)
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches


@pytest.fixture
def vera4_plan():
    patches = build_all_vera4_patches()
    result = assemble_simulation_plan_from_patches(patches, strict=False)
    return result.plan


class TestVERA4GridCounts:
    """VERA4-specific grid quantity checks."""

    def test_8_grid_bands(self, vera4_plan):
        checks = check_grid_geometry_level(vera4_plan)
        band_check = next(c for c in checks if c.code == "grid.band_count")
        assert band_check.passed

    def test_72_physical_instances(self, vera4_plan):
        checks = check_grid_geometry_level(vera4_plan)
        inst_check = next(c for c in checks if c.code == "grid.instance_count")
        assert inst_check.passed

    def test_2_end_grids_inconel(self, vera4_plan):
        checks = check_grid_geometry_level(vera4_plan)
        end_check = next(c for c in checks if c.code == "grid.end_grid_count")
        assert end_check.passed

    def test_6_middle_grids_zircaloy(self, vera4_plan):
        checks = check_grid_geometry_level(vera4_plan)
        mid_check = next(c for c in checks if c.code == "grid.middle_grid_count")
        assert mid_check.passed


class TestVERA4GridGeometry:
    """VERA4 grid geometry structure checks."""

    def test_decorated_universes_exist(self, vera4_plan):
        checks = check_grid_geometry_level(vera4_plan)
        check = next(c for c in checks if c.code == "grid.decorated_universes_exist")
        assert check.passed

    def test_lattices_reference_decorated(self, vera4_plan):
        checks = check_grid_geometry_level(vera4_plan)
        check = next(c for c in checks if c.code == "grid.lattices_reference_decorated")
        assert check.passed

    def test_frame_cells_exist(self, vera4_plan):
        checks = check_grid_geometry_level(vera4_plan)
        check = next(c for c in checks if c.code == "grid.frame_cells_exist")
        assert check.passed

    def test_frame_regions_exist(self, vera4_plan):
        checks = check_grid_geometry_level(vera4_plan)
        check = next(c for c in checks if c.code == "grid.frame_regions_exist")
        assert check.passed

    def test_materials_in_catalog(self, vera4_plan):
        checks = check_grid_geometry_level(vera4_plan)
        check = next(c for c in checks if c.code == "grid.materials_in_catalog")
        assert check.passed

    def test_assembly_gap_no_grid_material(self, vera4_plan):
        checks = check_grid_geometry_level(vera4_plan)
        check = next(c for c in checks if c.code == "grid.assembly_gap_no_grid_material")
        assert check.passed

    def test_validator_passes(self, vera4_plan):
        checks = check_grid_geometry_level(vera4_plan)
        check = next(c for c in checks if c.code == "grid.validator_passes")
        assert check.passed

    def test_reachability_passes(self, vera4_plan):
        checks = check_grid_geometry_level(vera4_plan)
        check = next(c for c in checks if c.code == "grid.reachability_passes")
        assert check.passed


class TestVERA4FullAcceptance:
    """Full acceptance including grid level."""

    def test_all_levels_pass(self, vera4_plan):
        result = run_full_acceptance(vera4_plan)
        assert result.ok
        assert result.summary["failed"] == 0

    def test_grid_level_checks_present(self, vera4_plan):
        result = run_full_acceptance(vera4_plan)
        grid_checks = [c for c in result.checks if c.level == "F"]
        assert len(grid_checks) >= 10

    def test_total_checks_increased(self, vera4_plan):
        """Grid level must add checks beyond the original A-E levels."""
        result = run_full_acceptance(vera4_plan)
        # Original was ~30 without grid; grid adds ~12
        assert result.summary["total"] >= 40


class TestVERA3NoGridRegression:
    """VERA3 (or any model without grids) must not be flagged."""

    def test_plan_without_overlays_not_flagged(self):
        """A plan without axial_overlays should pass grid validation cleanly."""
        from openmc_agent.plan_builder.grid_geometry_validation import (
            validate_grid_geometry_materialization,
        )
        patches = build_all_vera4_patches()
        patches_no_grid = [p for p in patches if p.patch_type != "axial_overlays"]
        result = assemble_simulation_plan_from_patches(patches_no_grid, strict=False)
        val = validate_grid_geometry_materialization(result.plan)
        assert val.ok
        assert val.summary["active_grid_overlays"] == 0
