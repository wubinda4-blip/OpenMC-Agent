"""Tests for the Level 1 ``homogenized_open_region`` axial-overlay renderer.

These tests are benchmark-free: they exercise a generic rectangular assembly
whose pin universes carry explicit fuel / clad / coolant cells, plus spacer-grid
overlays, and confirm the renderer:

* no longer downgrades a supported homogenized overlay for "renderer support",
* splits the axial domain around each overlay,
* derives an overlay lattice that swaps only the open/coolant cell while
  preserving fuel / clad / tube solids,
* conservatively reuses ambiguous universes (e.g. a guide tube with both an
  inner channel and outer moderator),
* and still downgrades for unsupported overlaps / unresolved open regions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openmc_agent.assembly3d_guard import (
    assembly3d_overlay_issues,
    validate_assembly3d_plan,
)
from openmc_agent.axial_overlay import (
    compute_axial_segments,
    derive_overlay_universe_plan,
    overlay_is_structurally_renderable,
)
from openmc_agent.renderers.assembly import RectAssemblyRenderer
from openmc_agent.schemas import (
    AxialLayerSpec,
    AxialOverlaySpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    CoreSpec,
    ExecutionCheckSpec,
    LatticeSpec,
    NuclideSpec,
    PlotSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
    SimulationPlan,
    UniverseSpec,
)


# -- shared fixtures -------------------------------------------------------


def _fuel() -> ComplexMaterialSpec:
    return ComplexMaterialSpec(
        id="fuel", name="UO2 fuel", density_unit="g/cm3", density_value=10.4,
        composition=[NuclideSpec(name="U235", percent=4.0)],
    )


def _water() -> ComplexMaterialSpec:
    return ComplexMaterialSpec(
        id="water", name="coolant water", density_unit="g/cm3", density_value=1.0,
        chemical_formula="H2O",
    )


def _clad() -> ComplexMaterialSpec:
    return ComplexMaterialSpec(
        id="clad", name="Zircaloy clad", density_unit="g/cm3", density_value=6.5,
        composition=[NuclideSpec(name="Zr90", percent=1.0)],
    )


def _grid() -> ComplexMaterialSpec:
    return ComplexMaterialSpec(
        id="grid_inconel", name="Inconel grid alloy", density_unit="g/cm3", density_value=7.9,
        composition=[NuclideSpec(name="Fe56", percent=1.0)],
    )


def _materials():
    return [_fuel(), _water(), _clad(), _grid()]


def _cells():
    # fuel pin: fuel pellet + clad + single open coolant cell (derives)
    # guide tube: clad wall + INNER water + OUTER water (2 open -> conservative reuse)
    # water pin: single water cell (derives)
    return [
        CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel"),
        CellSpec(id="clad_cell", name="clad", fill_type="material", fill_id="clad"),
        CellSpec(id="coolant_cell", name="coolant", fill_type="material", fill_id="water"),
        CellSpec(id="tube_wall_cell", name="tube wall", fill_type="material", fill_id="clad"),
        CellSpec(id="guide_inner_water", name="inner channel", fill_type="material", fill_id="water"),
        CellSpec(id="guide_outer_water", name="outer moderator", fill_type="material", fill_id="water"),
        CellSpec(id="water_only_cell", name="water", fill_type="material", fill_id="water"),
    ]


def _universes():
    return [
        UniverseSpec(id="fuel_pin", name="fuel pin", cell_ids=["fuel_cell", "clad_cell", "coolant_cell"]),
        UniverseSpec(id="guide_tube", name="guide tube", cell_ids=["tube_wall_cell", "guide_inner_water", "guide_outer_water"]),
        UniverseSpec(id="water_pin", name="water", cell_ids=["water_only_cell"]),
    ]


def _lattice():
    return LatticeSpec(
        id="assembly_lattice", name="assembly", kind="rect", pitch_cm=(1.26, 1.26),
        universe_pattern=[
            ["fuel_pin", "fuel_pin", "guide_tube"],
            ["fuel_pin", "water_pin", "fuel_pin"],
            ["guide_tube", "fuel_pin", "fuel_pin"],
        ],
    )


def _overlay_plan(
    *,
    overlays: list[AxialOverlaySpec],
    layers: list[AxialLayerSpec] | None = None,
) -> SimulationPlan:
    if layers is None:
        layers = [
            AxialLayerSpec(id="nozzle", name="nozzle", z_min_cm=0.0, z_max_cm=5.0,
                           fill={"type": "material", "id": "water"}),
            AxialLayerSpec(id="fuel", name="fuel region", z_min_cm=5.0, z_max_cm=100.0,
                           fill={"type": "lattice", "id": "assembly_lattice"}),
            AxialLayerSpec(id="plenum", name="plenum", z_min_cm=100.0, z_max_cm=110.0,
                           fill={"type": "material", "id": "water"}),
        ]
    model = ComplexModelSpec(
        name="overlay assembly", kind="assembly", materials=_materials(),
        cells=_cells(), universes=_universes(), lattices=[_lattice()],
        core=CoreSpec(
            id="core", name="core", lattice_id="assembly_lattice", boundary="reflective",
            axial_layers=layers, axial_overlays=overlays,
        ),
        settings=RunSettingsSpec(batches=4, inactive=1, particles=10),
    )
    return SimulationPlan(
        schema_version="simulation_plan.v2", complex_model=model,
        capability_report=RenderCapabilityReport(is_executable=False, supported_renderer="none"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(3.78, 3.78), filename="a.png")],
        execution_check=ExecutionCheckSpec(settings=RunSettingsSpec(batches=4, inactive=1, particles=10)),
    )


def _homogenized_overlay(
    *, oid="grid1", z_min=20.0, z_max=21.0, material_id="grid_inconel",
    target="assembly_lattice", through_path=True,
) -> AxialOverlaySpec:
    return AxialOverlaySpec(
        id=oid, overlay_kind="spacer_grid", z_min_cm=z_min, z_max_cm=z_max,
        target_lattice_id=target, material_id=material_id,
        geometry_mode="homogenized_open_region", through_path_preserved=through_path,
    )


# -- 1. supported overlay no longer triggers renderer-support downgrade -----


def test_supported_homogenized_overlay_is_renderable() -> None:
    plan = _overlay_plan(overlays=[_homogenized_overlay()])
    codes = {i.code for i in validate_assembly3d_plan(plan, requirement="3D assembly with spacer grids")}
    assert "assembly3d.axial_overlay_requires_renderer_support" not in codes

    capability = RectAssemblyRenderer().can_render(plan)
    assert capability.renderability in {"exportable", "runnable"}
    assert "axial_overlays" in capability.executable_subsystems
    # Level 1 fidelity is announced honestly in the warnings.
    assert any("Level 1" in w for w in capability.warnings)


# -- 2/3. axial segmentation around overlays ------------------------------


def test_single_overlay_splits_fuel_region_into_three_segments() -> None:
    plan = _overlay_plan(overlays=[_homogenized_overlay(z_min=20.0, z_max=25.0)])
    segments = compute_axial_segments(plan.complex_model)
    fuel_segs = [s for s in segments if s.layer.id == "fuel"]
    assert len(fuel_segs) == 3
    assert fuel_segs[0].overlay is None and fuel_segs[0].z_min == 5.0 and fuel_segs[0].z_max == 20.0
    assert fuel_segs[1].overlay is not None and fuel_segs[1].z_min == 20.0 and fuel_segs[1].z_max == 25.0
    assert fuel_segs[2].overlay is None and fuel_segs[2].z_min == 25.0 and fuel_segs[2].z_max == 100.0


def test_multiple_overlays_split_correctly() -> None:
    plan = _overlay_plan(overlays=[
        _homogenized_overlay(oid="g1", z_min=10.0, z_max=12.0),
        _homogenized_overlay(oid="g2", z_min=50.0, z_max=55.0),
    ])
    segments = compute_axial_segments(plan.complex_model)
    fuel_segs = [s for s in segments if s.layer.id == "fuel"]
    # 5 fuel segments: 5-10, 10-12, 12-50, 50-55, 55-100
    assert len(fuel_segs) == 5
    assert [s.overlay is not None for s in fuel_segs] == [False, True, False, True, False]


def test_overlay_does_not_affect_non_target_axial_layers() -> None:
    plan = _overlay_plan(overlays=[_homogenized_overlay(z_min=20.0, z_max=21.0)])
    segments = compute_axial_segments(plan.complex_model)
    # nozzle and plenum layers are material-filled (water), not the overlay target.
    nozzle = [s for s in segments if s.layer.id == "nozzle"]
    plenum = [s for s in segments if s.layer.id == "plenum"]
    assert len(nozzle) == 1 and nozzle[0].overlay is None
    assert len(plenum) == 1 and plenum[0].overlay is None
    # Rendered script keeps the water fill on nozzle/plenum, not a grid material.
    script = RectAssemblyRenderer().render(plan, Path("/tmp/_ov_nontarget")).script
    assert "materials_by_id['water']" in script
    assert "materials_by_id['grid_inconel']" in script  # only inside the overlay cell


# -- 5/6/7. derived overlay lattice preserves pin map + protected solids ---


def test_derived_overlay_lattice_preserves_shape_and_pin_counts() -> None:
    plan = _overlay_plan(overlays=[_homogenized_overlay()])
    base = plan.complex_model.lattices[0]
    fuel_positions = sum(row.count("fuel_pin") for row in base.universe_pattern)
    script = RectAssemblyRenderer().render(plan, Path("/tmp/_ov_shape")).script
    # The derived fuel-pin overlay universe fills every original fuel position.
    assert script.count("universes['fuel_pin__overlay_grid1']") >= fuel_positions
    # guide_tube (2 open cells) is reused unchanged at its original positions.
    assert "universes['guide_tube']" in script
    # Pitch / lower_left inherited from the base lattice.
    assert "overlay_lattice_grid1.pitch = (1.26, 1.26)" in script


def test_derived_overlay_universe_preserves_protected_cells() -> None:
    plan = _overlay_plan(overlays=[_homogenized_overlay()])
    overlay = plan.complex_model.core.axial_overlays[0]
    plans, unresolved = derive_overlay_universe_plan(overlay, plan.complex_model)
    assert unresolved == []
    fuel_plan = next(p for p in plans if p.base_universe_id == "fuel_pin")
    assert fuel_plan.derived_universe_id == "fuel_pin__overlay_grid1"
    assert fuel_plan.open_cell_id == "coolant_cell"  # only the open cell swaps
    # Rendered: fuel + clad cells are reused; a single new overlay coolant cell appears.
    script = RectAssemblyRenderer().render(plan, Path("/tmp/_ov_protected")).script
    assert "cells['fuel_cell'], cells['clad_cell'], overlay_cell_coolant_cell__grid1" in script
    assert "fill=materials_by_id['grid_inconel']" in script


def test_guide_tube_with_two_open_cells_is_conserved() -> None:
    """A guide tube with inner channel + outer moderator (2 open cells) cannot be
    safely split, so the base universe is reused unchanged (through-path kept)."""
    plan = _overlay_plan(overlays=[_homogenized_overlay()])
    overlay = plan.complex_model.core.axial_overlays[0]
    plans, _ = derive_overlay_universe_plan(overlay, plan.complex_model)
    guide_plan = next(p for p in plans if p.base_universe_id == "guide_tube")
    assert guide_plan.reuse_base is True
    assert guide_plan.derived_universe_id is None  # not altered -> through-path preserved
    # No full material replacement of the guide tube universe.
    script = RectAssemblyRenderer().render(plan, Path("/tmp/_ov_guide")).script
    assert "universes['guide_tube']" in script  # base reused in overlay lattice


# -- 8. unsupported overlapping overlays downgrade safely -----------------


def test_overlapping_overlays_with_different_material_downgrade() -> None:
    grid2 = ComplexMaterialSpec(
        id="grid_zr", name="Zircaloy grid", density_unit="g/cm3", density_value=6.5,
        composition=[NuclideSpec(name="Zr90", percent=1.0)],
    )
    plan = _overlay_plan(overlays=[
        _homogenized_overlay(oid="g1", z_min=20.0, z_max=30.0, material_id="grid_inconel"),
        _homogenized_overlay(oid="g2", z_min=25.0, z_max=35.0, material_id="grid_zr"),
    ])
    plan.complex_model.materials.append(grid2)
    codes = {i.code for i in assembly3d_overlay_issues(plan.complex_model)}
    assert "assembly3d.axial_overlay_overlap_unsupported" in codes
    capability = RectAssemblyRenderer().can_render(plan)
    assert capability.renderability == "skeleton"


# -- 9. missing open region unresolved -------------------------------------


def test_overlay_open_region_unresolved_when_no_open_cell() -> None:
    # Build a universe whose only cell is a fuel solid (no open region).
    cells = [CellSpec(id="solid_fuel_cell", name="fuel", fill_type="material", fill_id="fuel")]
    universes = [UniverseSpec(id="solid_pin", name="solid", cell_ids=["solid_fuel_cell"])]
    lattice = LatticeSpec(
        id="assembly_lattice", name="assembly", kind="rect", pitch_cm=(1.26, 1.26),
        universe_pattern=[["solid_pin", "solid_pin"], ["solid_pin", "solid_pin"]],
    )
    model = ComplexModelSpec(
        name="solid", kind="assembly", materials=_materials(),
        cells=cells, universes=universes, lattices=[lattice],
        core=CoreSpec(
            id="core", name="core", lattice_id="assembly_lattice", boundary="reflective",
            axial_layers=[AxialLayerSpec(id="fuel", name="fuel", z_min_cm=0.0, z_max_cm=10.0,
                                         fill={"type": "lattice", "id": "assembly_lattice"})],
            axial_overlays=[_homogenized_overlay(z_min=2.0, z_max=3.0)],
        ),
        settings=RunSettingsSpec(batches=4, inactive=1, particles=10),
    )
    codes = {i.code for i in assembly3d_overlay_issues(model)}
    assert "assembly3d.axial_overlay_open_region_unresolved" in codes
    plan = SimulationPlan(
        schema_version="simulation_plan.v2", complex_model=model,
        capability_report=RenderCapabilityReport(is_executable=False, supported_renderer="none"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(2.52, 2.52), filename="a.png")],
        execution_check=ExecutionCheckSpec(settings=RunSettingsSpec(batches=4, inactive=1, particles=10)),
    )
    capability = RectAssemblyRenderer().can_render(plan)
    assert capability.renderability == "skeleton"


# -- 10. VERA3-like smoke test (no VERA3 facts in production code) ---------


def test_vera3_like_fixture_renders_level1_overlay() -> None:
    """A generic 3D assembly with multiple spacer-grid overlays renders to an
    exportable model whose script carries the axial segmentation."""
    plan = _overlay_plan(overlays=[
        _homogenized_overlay(oid="grid_bottom", z_min=20.0, z_max=21.0),
        _homogenized_overlay(oid="grid_mid", z_min=55.0, z_max=56.0),
        _homogenized_overlay(oid="grid_top", z_min=90.0, z_max=91.0),
    ])
    codes = {i.code for i in validate_assembly3d_plan(plan, requirement="3D assembly with spacer grids")}
    # No geometric errors and no renderer-support downgrade.
    for bad in (
        "assembly3d.axial_overlay_requires_renderer_support",
        "assembly3d.spacer_grid_material_slab",
        "assembly3d.pin_through_path_missing",
    ):
        assert bad not in codes

    capability = RectAssemblyRenderer().can_render(plan)
    assert capability.renderability in {"exportable", "runnable"}

    script = RectAssemblyRenderer().render(plan, Path("/tmp/_ov_vera3like")).script
    compile(script, "model.py", "exec")  # syntactically valid Python
    # Three overlay lattices + three derived fuel-pin universes.
    assert "overlay_lattice_grid_bottom" in script
    assert "overlay_lattice_grid_mid" in script
    assert "overlay_lattice_grid_top" in script
    assert script.count("fill=overlay_lattice_grid_") == 3  # one segment each
    assert "fuel_pin__overlay_grid_bottom" in script


# -- regression: skeleton / unsupported modes still downgrade --------------


def test_explicit_bars_mode_still_requires_renderer_support() -> None:
    overlay = AxialOverlaySpec(
        id="g_explicit", overlay_kind="spacer_grid", z_min_cm=20.0, z_max_cm=21.0,
        target_lattice_id="assembly_lattice", material_id="grid_inconel",
        geometry_mode="explicit_bars", through_path_preserved=True,
    )
    plan = _overlay_plan(overlays=[overlay])
    codes = {i.code for i in assembly3d_overlay_issues(plan.complex_model)}
    assert "assembly3d.axial_overlay_requires_renderer_support" in codes
    assert overlay_is_structurally_renderable(overlay, plan.complex_model) is False
