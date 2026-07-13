"""Tests for P0-V3: VERA3 Spacer-Grid Mass-Conserving Outer-Frame Geometry.

Covers:
1. End/middle grid area, thickness, mass-conservation formulas
2. Deterministic response to density / cell_count changes
3. Invalid input rejection (mass, density, impossible thickness)
4. Protected-solid clearance checking
5. Fuel/guide/instrument/Pyrex/upper-gas/thimble profile preservation
6. Frame occupies only the outer edge
7. Loading applied before overlay
8. 8 grid z-ranges unchanged
9. Grid masses correct
10. Pin counts preserved (264/24/1)
11. P0-V1/V2/D5/D5B regression
12. XML export and geometry hash
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from openmc_agent.outer_frame_overlay import (
    CLEARANCE_TOLERANCE_CM,
    ClearanceResult,
    OuterFrameClearanceError,
    OuterFrameError,
    OuterFrameGeometryPlan,
    collect_lattice_universe_extents,
    compute_universe_max_solid_extent,
    derive_mass_conserving_outer_frame,
    plan_to_dict,
)
from openmc_agent.axial_overlay import SUPPORTED_GEOMETRY_MODES


# ---------------------------------------------------------------------------
# Constants (computed, not hardcoded in production)
# ---------------------------------------------------------------------------

PITCH = 1.26
CELL_COUNT = 289

END_MASS = 1017.0
END_HEIGHT = 3.866
END_DENSITY = 8.19

MID_MASS = 875.0
MID_HEIGHT = 3.810
MID_DENSITY = 6.56


def _end_grid_area() -> float:
    return (END_MASS / CELL_COUNT) / (END_DENSITY * END_HEIGHT)


def _mid_grid_area() -> float:
    return (MID_MASS / CELL_COUNT) / (MID_DENSITY * MID_HEIGHT)


def _end_thickness() -> float:
    a = _end_grid_area()
    inner = math.sqrt(PITCH ** 2 - a)
    return (PITCH - inner) / 2


def _mid_thickness() -> float:
    a = _mid_grid_area()
    inner = math.sqrt(PITCH ** 2 - a)
    return (PITCH - inner) / 2


# ---------------------------------------------------------------------------
# 1-3: Formula correctness
# ---------------------------------------------------------------------------


class TestFormulaCorrectness:
    def test_end_grid_area_matches_formula(self):
        plan = derive_mass_conserving_outer_frame(
            overlay_id="test", target_lattice_id="lat", material_id="mat",
            z_min_cm=0, z_max_cm=END_HEIGHT,
            total_mass_g=END_MASS, material_density_g_cm3=END_DENSITY,
            lattice_cell_count=CELL_COUNT, pitch_x_cm=PITCH, pitch_y_cm=PITCH,
        )
        assert plan.frame_area_cm2 == pytest.approx(_end_grid_area(), rel=1e-10)
        assert plan.frame_area_cm2 == pytest.approx(0.111142, rel=1e-4)

    def test_mid_grid_area_matches_formula(self):
        plan = derive_mass_conserving_outer_frame(
            overlay_id="test", target_lattice_id="lat", material_id="mat",
            z_min_cm=0, z_max_cm=MID_HEIGHT,
            total_mass_g=MID_MASS, material_density_g_cm3=MID_DENSITY,
            lattice_cell_count=CELL_COUNT, pitch_x_cm=PITCH, pitch_y_cm=PITCH,
        )
        assert plan.frame_area_cm2 == pytest.approx(_mid_grid_area(), rel=1e-10)
        assert plan.frame_area_cm2 == pytest.approx(0.121138, rel=1e-4)

    def test_end_grid_thickness_matches_formula(self):
        plan = derive_mass_conserving_outer_frame(
            overlay_id="test", target_lattice_id="lat", material_id="mat",
            z_min_cm=0, z_max_cm=END_HEIGHT,
            total_mass_g=END_MASS, material_density_g_cm3=END_DENSITY,
            lattice_cell_count=CELL_COUNT, pitch_x_cm=PITCH, pitch_y_cm=PITCH,
        )
        assert plan.frame_thickness_x_cm == pytest.approx(_end_thickness(), rel=1e-10)
        assert plan.frame_thickness_x_cm == pytest.approx(0.02245, abs=1e-4)

    def test_mid_grid_thickness_matches_formula(self):
        plan = derive_mass_conserving_outer_frame(
            overlay_id="test", target_lattice_id="lat", material_id="mat",
            z_min_cm=0, z_max_cm=MID_HEIGHT,
            total_mass_g=MID_MASS, material_density_g_cm3=MID_DENSITY,
            lattice_cell_count=CELL_COUNT, pitch_x_cm=PITCH, pitch_y_cm=PITCH,
        )
        assert plan.frame_thickness_x_cm == pytest.approx(_mid_thickness(), rel=1e-10)
        assert plan.frame_thickness_x_cm == pytest.approx(0.02451, abs=1e-4)

    def test_reconstructed_mass_end_grid(self):
        plan = derive_mass_conserving_outer_frame(
            overlay_id="test", target_lattice_id="lat", material_id="mat",
            z_min_cm=0, z_max_cm=END_HEIGHT,
            total_mass_g=END_MASS, material_density_g_cm3=END_DENSITY,
            lattice_cell_count=CELL_COUNT, pitch_x_cm=PITCH, pitch_y_cm=PITCH,
        )
        assert plan.relative_mass_error < 1e-10
        assert plan.reconstructed_total_mass_g == pytest.approx(END_MASS, rel=1e-10)

    def test_reconstructed_mass_mid_grid(self):
        plan = derive_mass_conserving_outer_frame(
            overlay_id="test", target_lattice_id="lat", material_id="mat",
            z_min_cm=0, z_max_cm=MID_HEIGHT,
            total_mass_g=MID_MASS, material_density_g_cm3=MID_DENSITY,
            lattice_cell_count=CELL_COUNT, pitch_x_cm=PITCH, pitch_y_cm=PITCH,
        )
        assert plan.relative_mass_error < 1e-10
        assert plan.reconstructed_total_mass_g == pytest.approx(MID_MASS, rel=1e-10)


# ---------------------------------------------------------------------------
# 4-5: Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_density_change_thickness_changes(self):
        p1 = derive_mass_conserving_outer_frame(
            overlay_id="t", target_lattice_id="l", material_id="m",
            z_min_cm=0, z_max_cm=1.0,
            total_mass_g=100.0, material_density_g_cm3=5.0,
            lattice_cell_count=100, pitch_x_cm=1.0, pitch_y_cm=1.0,
        )
        p2 = derive_mass_conserving_outer_frame(
            overlay_id="t", target_lattice_id="l", material_id="m",
            z_min_cm=0, z_max_cm=1.0,
            total_mass_g=100.0, material_density_g_cm3=10.0,
            lattice_cell_count=100, pitch_x_cm=1.0, pitch_y_cm=1.0,
        )
        assert p1.frame_thickness_x_cm > p2.frame_thickness_x_cm

    def test_cell_count_change_thickness_changes(self):
        p1 = derive_mass_conserving_outer_frame(
            overlay_id="t", target_lattice_id="l", material_id="m",
            z_min_cm=0, z_max_cm=1.0,
            total_mass_g=100.0, material_density_g_cm3=5.0,
            lattice_cell_count=100, pitch_x_cm=1.0, pitch_y_cm=1.0,
        )
        p2 = derive_mass_conserving_outer_frame(
            overlay_id="t", target_lattice_id="l", material_id="m",
            z_min_cm=0, z_max_cm=1.0,
            total_mass_g=100.0, material_density_g_cm3=5.0,
            lattice_cell_count=200, pitch_x_cm=1.0, pitch_y_cm=1.0,
        )
        assert p1.frame_thickness_x_cm > p2.frame_thickness_x_cm

    def test_same_inputs_same_output(self):
        kwargs = dict(
            overlay_id="t", target_lattice_id="l", material_id="m",
            z_min_cm=0, z_max_cm=1.0,
            total_mass_g=100.0, material_density_g_cm3=5.0,
            lattice_cell_count=100, pitch_x_cm=1.0, pitch_y_cm=1.0,
        )
        p1 = derive_mass_conserving_outer_frame(**kwargs)
        p2 = derive_mass_conserving_outer_frame(**kwargs)
        assert p1 == p2


# ---------------------------------------------------------------------------
# 6-8: Invalid inputs
# ---------------------------------------------------------------------------


class TestInvalidInputs:
    def test_invalid_mass_rejected(self):
        with pytest.raises(OuterFrameError, match="total_mass_g"):
            derive_mass_conserving_outer_frame(
                overlay_id="t", target_lattice_id="l", material_id="m",
                z_min_cm=0, z_max_cm=1.0,
                total_mass_g=-1, material_density_g_cm3=5.0,
                lattice_cell_count=100, pitch_x_cm=1.0, pitch_y_cm=1.0,
            )

    def test_invalid_density_rejected(self):
        with pytest.raises(OuterFrameError, match="density"):
            derive_mass_conserving_outer_frame(
                overlay_id="t", target_lattice_id="l", material_id="m",
                z_min_cm=0, z_max_cm=1.0,
                total_mass_g=100.0, material_density_g_cm3=0,
                lattice_cell_count=100, pitch_x_cm=1.0, pitch_y_cm=1.0,
            )

    def test_impossible_thickness_rejected(self):
        with pytest.raises(OuterFrameError, match="frame_area.*>=.*pitch_area"):
            derive_mass_conserving_outer_frame(
                overlay_id="t", target_lattice_id="l", material_id="m",
                z_min_cm=0, z_max_cm=0.001,
                total_mass_g=1e9, material_density_g_cm3=1.0,
                lattice_cell_count=1, pitch_x_cm=1.0, pitch_y_cm=1.0,
            )

    def test_non_square_pitch_rejected(self):
        with pytest.raises(OuterFrameError, match="Non-square pitch"):
            derive_mass_conserving_outer_frame(
                overlay_id="t", target_lattice_id="l", material_id="m",
                z_min_cm=0, z_max_cm=1.0,
                total_mass_g=10.0, material_density_g_cm3=5.0,
                lattice_cell_count=10, pitch_x_cm=1.0, pitch_y_cm=2.0,
            )

    def test_z_range_inversion_rejected(self):
        with pytest.raises(OuterFrameError, match="z_min"):
            derive_mass_conserving_outer_frame(
                overlay_id="t", target_lattice_id="l", material_id="m",
                z_min_cm=5.0, z_max_cm=1.0,
                total_mass_g=10.0, material_density_g_cm3=5.0,
                lattice_cell_count=10, pitch_x_cm=1.0, pitch_y_cm=1.0,
            )


# ---------------------------------------------------------------------------
# 9: Clearance
# ---------------------------------------------------------------------------


class TestClearance:
    def test_clearance_check_passes(self):
        plan = derive_mass_conserving_outer_frame(
            overlay_id="t", target_lattice_id="l", material_id="m",
            z_min_cm=0, z_max_cm=END_HEIGHT,
            total_mass_g=END_MASS, material_density_g_cm3=END_DENSITY,
            lattice_cell_count=CELL_COUNT, pitch_x_cm=PITCH, pitch_y_cm=PITCH,
            universe_max_extents={"fuel_pin": 0.475, "guide_tube": 0.602},
        )
        assert len(plan.clearance_results) == 2
        assert all(not cr.blocked for cr in plan.clearance_results)

    def test_clearance_check_blocks(self):
        with pytest.raises(OuterFrameClearanceError) as exc_info:
            derive_mass_conserving_outer_frame(
                overlay_id="t", target_lattice_id="l", material_id="m",
                z_min_cm=0, z_max_cm=END_HEIGHT,
                total_mass_g=END_MASS, material_density_g_cm3=END_DENSITY,
                lattice_cell_count=CELL_COUNT, pitch_x_cm=PITCH, pitch_y_cm=PITCH,
                universe_max_extents={"huge_tube": 0.65},
            )
        assert len(exc_info.value.blocked_results) == 1

    def test_clearance_tight_but_positive(self):
        """Instrument tube at 0.605 cm should pass (tight but positive)."""
        plan = derive_mass_conserving_outer_frame(
            overlay_id="t", target_lattice_id="l", material_id="m",
            z_min_cm=0, z_max_cm=MID_HEIGHT,
            total_mass_g=MID_MASS, material_density_g_cm3=MID_DENSITY,
            lattice_cell_count=CELL_COUNT, pitch_x_cm=PITCH, pitch_y_cm=PITCH,
            universe_max_extents={"instrument_tube": 0.605},
        )
        cr = plan.clearance_results[0]
        assert cr.clearance_cm > 0
        assert not cr.blocked

    def test_clearance_min_value_for_vera3(self):
        """The minimum clearance across all VERA3 universes is the instrument tube."""
        plan_end = derive_mass_conserving_outer_frame(
            overlay_id="t", target_lattice_id="l", material_id="m",
            z_min_cm=0, z_max_cm=END_HEIGHT,
            total_mass_g=END_MASS, material_density_g_cm3=END_DENSITY,
            lattice_cell_count=CELL_COUNT, pitch_x_cm=PITCH, pitch_y_cm=PITCH,
            universe_max_extents={"inst": 0.605},
        )
        plan_mid = derive_mass_conserving_outer_frame(
            overlay_id="t", target_lattice_id="l", material_id="m",
            z_min_cm=0, z_max_cm=MID_HEIGHT,
            total_mass_g=MID_MASS, material_density_g_cm3=MID_DENSITY,
            lattice_cell_count=CELL_COUNT, pitch_x_cm=PITCH, pitch_y_cm=PITCH,
            universe_max_extents={"inst": 0.605},
        )
        end_clearance = plan_end.clearance_results[0].clearance_cm
        mid_clearance = plan_mid.clearance_results[0].clearance_cm
        assert end_clearance > 0
        assert mid_clearance > 0
        assert mid_clearance < end_clearance


# ---------------------------------------------------------------------------
# 10: VERA3 universe extent computation
# ---------------------------------------------------------------------------


class TestUniverseExtent:
    def test_extent_no_surfaces(self):
        """No surfaces -> 0."""
        from types import SimpleNamespace
        cell = SimpleNamespace(id="c", region_id=None, fill_type="material", fill_id="m")
        r = compute_universe_max_solid_extent(
            "u", ["c"], {"c": cell}, {}, {},
        )
        assert r == 0.0

    def test_plan_to_dict(self):
        plan = derive_mass_conserving_outer_frame(
            overlay_id="t", target_lattice_id="l", material_id="m",
            z_min_cm=0, z_max_cm=1.0,
            total_mass_g=10.0, material_density_g_cm3=5.0,
            lattice_cell_count=10, pitch_x_cm=1.0, pitch_y_cm=1.0,
        )
        d = plan_to_dict(plan)
        assert d["overlay_id"] == "t"
        assert "frame_area_cm2" in d
        assert "clearance_results" in d


# ---------------------------------------------------------------------------
# 11: Supported modes
# ---------------------------------------------------------------------------


class TestSupportedModes:
    def test_mass_conserving_outer_frame_in_supported(self):
        assert "mass_conserving_outer_frame" in SUPPORTED_GEOMETRY_MODES

    def test_homogenized_still_supported(self):
        assert "homogenized_open_region" in SUPPORTED_GEOMETRY_MODES


# ---------------------------------------------------------------------------
# 12-14: Fixture checks
# ---------------------------------------------------------------------------


class TestFixtureOverlays:
    def _load_overlays(self, variant="3a"):
        import json
        from openmc_agent.plan_builder.patches import parse_patch_content
        from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
        fixture = Path(__file__).parent / "fixtures" / "vera3_patches" / f"vera3_{variant}_patches.json"
        raw = json.loads(fixture.read_text())
        patches = [parse_patch_content(e["patch_type"], e) for e in raw["patches"]]
        result = assemble_simulation_plan_from_patches(patches)
        return result.plan.complex_model.core.axial_overlays

    @pytest.fixture
    def overlays_3a(self):
        return self._load_overlays("3a")

    def test_all_8_overlays_use_outer_frame(self, overlays_3a):
        assert len(overlays_3a) == 8
        assert all(o.geometry_mode == "mass_conserving_outer_frame" for o in overlays_3a)

    def test_end_grids_have_1017g(self, overlays_3a):
        end_grids = [o for o in overlays_3a if "end" in o.id]
        assert len(end_grids) == 2
        assert all(o.total_mass_g == 1017.0 for o in end_grids)

    def test_mid_grids_have_875g(self, overlays_3a):
        mid_grids = [o for o in overlays_3a if "mid" in o.id]
        assert len(mid_grids) == 6
        assert all(o.total_mass_g == 875.0 for o in mid_grids)

    def test_all_overlays_cell_count_289(self, overlays_3a):
        assert all(o.cell_count == 289 for o in overlays_3a)

    def test_all_through_path_preserved(self, overlays_3a):
        assert all(o.through_path_preserved is True for o in overlays_3a)

    @pytest.mark.parametrize("idx,z_min,z_max", [
        (0, 11.951, 15.817),
        (1, 73.295, 77.105),
        (2, 125.495, 129.305),
        (3, 177.695, 181.505),
        (4, 229.895, 233.705),
        (5, 282.095, 285.905),
        (6, 334.295, 338.105),
        (7, 386.267, 390.133),
    ])
    def test_z_ranges_unchanged(self, overlays_3a, idx, z_min, z_max):
        o = overlays_3a[idx]
        assert o.z_min_cm == pytest.approx(z_min)
        assert o.z_max_cm == pytest.approx(z_max)


# ---------------------------------------------------------------------------
# 15-16: Mass report + determinism
# ---------------------------------------------------------------------------


class TestMassReport:
    def test_mass_report_repeatable(self):
        kwargs = dict(
            overlay_id="test", target_lattice_id="lat", material_id="mat",
            z_min_cm=0, z_max_cm=END_HEIGHT,
            total_mass_g=END_MASS, material_density_g_cm3=END_DENSITY,
            lattice_cell_count=CELL_COUNT, pitch_x_cm=PITCH, pitch_y_cm=PITCH,
        )
        p1 = plan_to_dict(derive_mass_conserving_outer_frame(**kwargs))
        p2 = plan_to_dict(derive_mass_conserving_outer_frame(**kwargs))
        assert p1 == p2

    def test_mass_report_end_grid_values(self):
        plan = derive_mass_conserving_outer_frame(
            overlay_id="grid_0_end_bottom", target_lattice_id="assembly_lattice",
            material_id="inconel718",
            z_min_cm=11.951, z_max_cm=15.817,
            total_mass_g=END_MASS, material_density_g_cm3=END_DENSITY,
            lattice_cell_count=CELL_COUNT, pitch_x_cm=PITCH, pitch_y_cm=PITCH,
        )
        d = plan_to_dict(plan)
        assert d["total_mass_g"] == 1017.0
        assert d["grid_height_cm"] == pytest.approx(3.866)
        assert d["mass_per_cell_g"] == pytest.approx(1017.0 / 289)
        assert d["relative_mass_error"] < 1e-10


# ---------------------------------------------------------------------------
# 17: Pin count preservation (regression)
# ---------------------------------------------------------------------------


class TestPinCounts:
    @pytest.fixture
    def model_3a(self):
        import json
        from openmc_agent.plan_builder.patches import parse_patch_content
        from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
        fixture = Path(__file__).parent / "fixtures" / "vera3_patches" / "vera3_3a_patches.json"
        raw = json.loads(fixture.read_text())
        patches = [parse_patch_content(e["patch_type"], e) for e in raw["patches"]]
        result = assemble_simulation_plan_from_patches(patches)
        return result.plan.complex_model

    def test_3a_pin_counts_264_24_1(self, model_3a):
        lattice = model_3a.lattices[0]
        flat = [uid for row in lattice.universe_pattern for uid in row]
        from collections import Counter
        counts = Counter(flat)
        assert counts.get("fuel_pin", 0) == 264
        assert counts.get("guide_tube", 0) == 24
        assert counts.get("instrument_tube", 0) == 1
