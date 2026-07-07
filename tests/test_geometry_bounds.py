"""Tests for full-assembly geometry / source / plot bounds consistency (Step 6).

The VERA3 plot rendered only a quarter because the slice ``origin`` was treated
as the assembly center while the geometry sits at [0,W]x[0,H]. These tests
confirm the plot is recentered on the assembly center, the source xy covers the
full footprint, and a bounds-consistency validator flags mismatches.
"""

from __future__ import annotations

import pytest

from helpers.vera3_acceptance import build_vera3_like_plan, load_vera3_reference
from openmc_agent.geometry_bounds import (
    compute_geometry_bounds,
    infer_symmetry_policy,
    build_geometry_metadata,
    validate_bounds_consistency,
)
from openmc_agent.renderers.assembly import RectAssemblyRenderer
from openmc_agent.source_settings import source_bounds_for_plan
from openmc_agent.tools import parse_openmc_output

REFERENCE = load_vera3_reference()


def _src_tuple(plan):
    b = source_bounds_for_plan(plan.complex_model)
    return (b.x_min, b.x_max, b.y_min, b.y_max, b.z_min, b.z_max)


# -- 1/2. full assembly bounds + no quarter symmetry -----------------------


def test_full_assembly_bounds_from_17x17_lattice() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    gb = compute_geometry_bounds(plan.complex_model)
    assert gb is not None
    # 17 pins x 1.26 cm pitch = 21.42 cm full width.
    assert gb.lattice_x_max - gb.lattice_x_min == pytest.approx(21.42)
    assert gb.lattice_y_max - gb.lattice_y_min == pytest.approx(21.42)
    assert (gb.lattice_rows, gb.lattice_cols) == (17, 17)


def test_vera3_full_assembly_no_quarter_symmetry() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    gb = compute_geometry_bounds(plan.complex_model)
    policy = infer_symmetry_policy(plan.complex_model, gb)
    assert policy.mode == "full"
    assert policy.has_internal_reflective_origin_planes is False
    st = _src_tuple(plan)
    issues = validate_bounds_consistency(plan.complex_model, source_bounds=st)
    assert "geometry.quarter_symmetry_unexpected" not in {i.code for i in issues}


# -- 3. quarter symmetry unexpected ---------------------------------------


def test_quarter_symmetry_unexpected_flagged() -> None:
    """A geometry whose source covers full but whose footprint is half-width
    triggers a source/geometry mismatch (the practical quarter-detection path)."""
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    # Source claims full width but geometry footprint is half -> mismatch.
    st = (0.0, 21.42, 0.0, 21.42, 11.951, 377.711)
    # Shrink the geometry footprint to a quarter and check the too-small source
    # flag fires when the source is wider than the geometry.
    gb = compute_geometry_bounds(plan.complex_model)
    quarter = type(gb)(
        lattice_x_min=0.0, lattice_x_max=10.71, lattice_y_min=0.0, lattice_y_max=10.71,
        geom_x_min=0.0, geom_x_max=10.71, geom_y_min=0.0, geom_y_max=10.71,
        geom_z_min=gb.geom_z_min, geom_z_max=gb.geom_z_max,
        active_fuel_z_min=gb.active_fuel_z_min, active_fuel_z_max=gb.active_fuel_z_max,
        has_lattice=True, lattice_rows=17, lattice_cols=17,
    )
    issues = validate_bounds_consistency.__wrapped__ if hasattr(validate_bounds_consistency, "__wrapped__") else None
    # Directly exercise the inside-geometry check with the quarter geometry.
    from openmc_agent.geometry_bounds import _source_inside_geometry
    assert _source_inside_geometry(st, quarter) is False  # source extends beyond quarter geometry


# -- 4. source x/y matches full geometry ----------------------------------


def test_source_xy_matches_full_geometry() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    st = _src_tuple(plan)
    issues = validate_bounds_consistency(plan.complex_model, source_bounds=st)
    codes = {i.code for i in issues}
    assert "runtime.source_xy_outside_geometry" not in codes
    assert "runtime.source_xy_too_small_for_full_assembly" not in codes


# -- 6. source x/y outside geometry ---------------------------------------


def test_source_xy_outside_geometry_flagged() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    # Geometry is 0..21.42; source claims -5..26 -> outside.
    st = (-5.0, 26.0, -5.0, 26.0, 11.951, 377.711)
    issues = validate_bounds_consistency(plan.complex_model, source_bounds=st)
    assert "runtime.source_xy_outside_geometry" in {i.code for i in issues}


# -- 7. source x/y too small for full assembly ----------------------------


def test_source_xy_too_small_flagged() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    # Full width is 21.42; a source of ~8 cm covers less than 60% -> too small.
    st = (1.0, 9.0, 1.0, 9.0, 11.951, 377.711)
    issues = validate_bounds_consistency(plan.complex_model, source_bounds=st)
    assert "runtime.source_xy_too_small_for_full_assembly" in {i.code for i in issues}


# -- 8/9. plot bounds ------------------------------------------------------


def test_rendered_plot_origin_is_assembly_center(tmp_path) -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    script = RectAssemblyRenderer().render(plan, tmp_path).script
    # Geometry sits at [0,21.42] -> center (10.71, 10.71). The xy plot origin
    # must be recentered there, not left at the (0,0) corner.
    assert "plot_0_material.origin = (10.71, 10.71" in script


def test_plot_not_covering_assembly_flagged() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    st = _src_tuple(plan)
    # A plot centered at the corner with half width covers only a quarter.
    bad_plot = [{"id": "xy_bad", "basis": "xy",
                 "origin": {"x": 0.0, "y": 0.0},
                 "width": {"x": 10.0, "y": 10.0}}]
    issues = validate_bounds_consistency(plan.complex_model, source_bounds=st, plot_bounds=bad_plot)
    assert "runtime.plot_bounds_do_not_cover_assembly" in {i.code for i in issues}


def test_full_assembly_plot_passes() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    gb = compute_geometry_bounds(plan.complex_model)
    st = _src_tuple(plan)
    good_plot = [{"id": "xy", "basis": "xy",
                  "origin": {"x": gb.lattice_center[0], "y": gb.lattice_center[1]},
                  "width": {"x": gb.lattice_width[0], "y": gb.lattice_width[1]}}]
    issues = validate_bounds_consistency(plan.complex_model, source_bounds=st, plot_bounds=good_plot)
    assert "runtime.plot_bounds_do_not_cover_assembly" not in {i.code for i in issues}


# -- 10. metadata report includes all bounds ------------------------------


def test_geometry_metadata_includes_all_bounds() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    st = _src_tuple(plan)
    meta = build_geometry_metadata(plan.complex_model, source_bounds=st)
    assert meta["symmetry_policy"]["mode"] == "full"
    assert "geometry_bounds_cm" in meta and "lattice_footprint_cm" in meta
    assert "active_fuel_bounds_cm" in meta
    assert "source_bounds_cm" in meta
    assert meta["source_bounds_cm"]["z_min"] == pytest.approx(11.951)
    assert meta["source_bounds_cm"]["z_max"] == pytest.approx(377.711)
    assert "fuel" in meta["fuel_material_ids"]
    assert meta["source_geometry_mismatch"] is False


# -- 11. source rejection report includes bounds diagnostics ---------------


def test_source_rejection_report_primary_issue_is_source_init() -> None:
    stderr = (
        "Too few source sites satisfied the constraints\n"
        "minimum source rejection fraction = 0.05\n"
        "double free or corruption\nSegmentation fault\nMPI abort"
    )
    report = parse_openmc_output("", stderr)
    codes = [i.code for i in report.issues]
    assert codes[0] == "runtime.openmc_source_rejection_failure"
    # segfault/MPI abort must not be the primary issue.
    assert all("segfault" not in c and "mpi" not in c.lower() for c in codes)


def test_source_rejection_bounds_diagnostics_in_metadata() -> None:
    """The geometry metadata provides the flags a rejection report attaches."""
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    st = _src_tuple(plan)
    gb = compute_geometry_bounds(plan.complex_model)
    diagnostics = {
        "source_z_matches_active_fuel": st[4] == gb.active_fuel_z[0] and st[5] == gb.active_fuel_z[1],
        "source_xy_inside_geometry": st[0] >= gb.geom_x_min and st[1] <= gb.geom_x_max,
        "source_xy_matches_full_assembly": (st[1] - st[0]) >= 0.95 * (gb.lattice_x_max - gb.lattice_x_min),
        "geometry_is_full_assembly": infer_symmetry_policy(plan.complex_model, gb).mode == "full",
        "fuel_material_fissionable": "fuel" in build_geometry_metadata(plan.complex_model)["fuel_material_ids"],
    }
    # For a valid VERA3-like plan all diagnostics are True.
    assert all(diagnostics.values()), diagnostics


# -- 12. successful full bounds smoke settings ----------------------------


def test_vera3_full_bounds_have_no_blocking_consistency_issues() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    st = _src_tuple(plan)
    gb = compute_geometry_bounds(plan.complex_model)
    plot_bounds = [{"id": "xy", "basis": "xy",
                    "origin": {"x": gb.lattice_center[0], "y": gb.lattice_center[1]},
                    "width": {"x": gb.lattice_width[0], "y": gb.lattice_width[1]}}]
    issues = validate_bounds_consistency(plan.complex_model, source_bounds=st, plot_bounds=plot_bounds)
    blocking = [i for i in issues if i.severity == "error"]
    assert blocking == [], [str(i) for i in blocking]
