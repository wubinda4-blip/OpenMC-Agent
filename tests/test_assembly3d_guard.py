"""Tests for the generic 3D-assembly / axial-geometry workflow guard.

These tests are intentionally benchmark-free: they exercise generic axial
signals (axial layers, spacer grids, explicit z ranges, nozzles) and confirm
the guard blocks 2D-assembly export when the requirement is genuinely 3D.
No VERA-specific facts are encoded anywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openmc_agent.assembly3d_guard import (
    Assembly3DFeatureFlags,
    detect_assembly_3d_features,
    validate_assembly3d_plan,
)
from openmc_agent.renderers import choose_renderer
from openmc_agent.renderers.assembly import RectAssemblyRenderer
from openmc_agent.schemas import (
    AssemblySpec,
    AxialLayerSpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    CoreSpec,
    ExecutionCheckSpec,
    LatticeLoadingSpec,
    LatticeSpec,
    NuclideSpec,
    PlotSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
    SimulationPlan,
    UniverseSpec,
)
from openmc_agent.validator import validate_simulation_plan


# -- detector ---------------------------------------------------------------


def test_detect_3d_assembly_with_axial_layers() -> None:
    flags = detect_assembly_3d_features(
        "Build a 3D assembly with axial layers from 0 to 365 cm"
    )
    assert flags.has_axial_geometry is True
    assert any("axial" in term for term in flags.matched_terms)


def test_detect_plain_2d_assembly_has_no_axial_signals() -> None:
    text = (
        "17x17 PWR UO2 fuel assembly, pitch 1.26 cm, reflective radial "
        "boundary, water moderator"
    )
    flags = detect_assembly_3d_features(text)
    assert flags.has_axial_geometry is False
    assert flags.has_spacer_grid is False
    assert flags.has_explicit_z_ranges is False
    assert flags.has_axial_components is False
    assert flags.matched_terms == []


def test_detect_spacer_grid_terms() -> None:
    flags = detect_assembly_3d_features(
        "model includes spacer grids, mid-grid and top grid with mixing vanes"
    )
    assert flags.has_spacer_grid is True
    assert "spacer grid" in flags.matched_terms


def test_detect_explicit_z_range_phrase() -> None:
    flags = detect_assembly_3d_features("axial region from 10.0 cm to 13.8 cm")
    assert flags.has_explicit_z_ranges is True
    assert flags.has_axial_geometry is True


def test_detect_z_min_z_max_keys() -> None:
    flags = detect_assembly_3d_features("axial layers: z_min=0.0, z_max=365.76")
    assert flags.has_explicit_z_ranges is True


def test_detect_axial_components_trigger_axial_geometry() -> None:
    flags = detect_assembly_3d_features(
        "top nozzle, bottom nozzle, plenum and end plug structure"
    )
    assert flags.has_axial_components is True
    assert flags.has_axial_geometry is True


def test_detect_accepts_dict_requirement() -> None:
    flags = detect_assembly_3d_features(
        {"requirement": "3D assembly with spacer grid and axial reflector"}
    )
    assert flags.has_axial_geometry is True
    assert flags.has_spacer_grid is True


def test_detect_chinese_grid_and_axial_terms() -> None:
    flags = detect_assembly_3d_features("包含定位格架与轴向反射层的三维组件")
    assert flags.has_spacer_grid is True
    assert flags.has_axial_geometry is True


def test_detect_returns_feature_flags_type() -> None:
    flags = detect_assembly_3d_features("3D assembly")
    assert isinstance(flags, Assembly3DFeatureFlags)


def test_detect_control_rod_insertion_is_axial() -> None:
    flags = detect_assembly_3d_features("control rod insertion at 25% axial height")
    assert flags.has_axial_geometry is True


# -- plan fixtures ----------------------------------------------------------


def _fuel() -> ComplexMaterialSpec:
    return ComplexMaterialSpec(
        id="fuel",
        name="UO2 fuel",
        density_unit="g/cm3",
        density_value=10.4,
        composition=[NuclideSpec(name="U235", percent=4.95), NuclideSpec(name="O16", percent=2.0)],
    )


def _moderator() -> ComplexMaterialSpec:
    return ComplexMaterialSpec(
        id="moderator",
        name="water",
        density_unit="g/cm3",
        density_value=1.0,
        chemical_formula="H2O",
    )


def _grid_material() -> ComplexMaterialSpec:
    return ComplexMaterialSpec(
        id="grid_material",
        name="grid alloy",
        density_unit="g/cm3",
        density_value=7.9,
        composition=[NuclideSpec(name="Fe56", percent=1.0)],
    )


def _materials() -> list[ComplexMaterialSpec]:
    return [_fuel(), _moderator(), _grid_material()]


def _assembly_plan(*, core: CoreSpec | None = None, grid_overlay: bool = False) -> SimulationPlan:
    """Minimal rectangular assembly plan; ``core`` adds axial layers when given."""
    cells = [
        CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel"),
        CellSpec(id="mod_cell", name="moderator", fill_type="material", fill_id="moderator"),
    ]
    universes = [
        UniverseSpec(id="fuel_pin", name="fuel pin", cell_ids=["fuel_cell"]),
        UniverseSpec(id="mod_pin", name="moderator pin", cell_ids=["mod_cell"]),
    ]
    lattices = [
        LatticeSpec(
            id="active_lattice",
            name="active",
            kind="rect",
            pitch_cm=(1.26, 1.26),
            universe_pattern=[["fuel_pin", "mod_pin"], ["mod_pin", "fuel_pin"]],
        )
    ]
    lattice_loadings: list[LatticeLoadingSpec] = []
    if grid_overlay:
        universes.append(
            UniverseSpec(id="grid_overlay_pin", name="grid overlay", cell_ids=["fuel_cell"])
        )
        lattice_loadings.append(
            LatticeLoadingSpec(
                id="grid_loading",
                base_lattice_id="active_lattice",
                derived_lattice_id="grid_lattice",
                overrides={"grid_overlay_pin": [(0, 0), (0, 1), (1, 0), (1, 1)]},
            )
        )
    model = ComplexModelSpec(
        name="test assembly",
        kind="assembly",
        materials=_materials(),
        cells=cells,
        universes=universes,
        lattices=lattices,
        lattice_loadings=lattice_loadings,
        assemblies=[
            AssemblySpec(
                id="assembly",
                name="root",
                lattice_id="active_lattice",
                boundary="reflective",
            )
        ],
        settings=RunSettingsSpec(batches=8, inactive=2, particles=80),
        core=core,
    )
    return SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=model,
        capability_report=RenderCapabilityReport(is_executable=False, supported_renderer="none"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(2.52, 2.52), filename="assembly.png")],
        execution_check=ExecutionCheckSpec(
            settings=RunSettingsSpec(batches=4, inactive=1, particles=20)
        ),
    )


def _grid_slab_layer() -> AxialLayerSpec:
    """A spacer-grid axial layer that fills the whole layer with one material."""
    return AxialLayerSpec(
        id="spacer_grid",
        name="spacer grid",
        z_min_cm=10.0,
        z_max_cm=13.8,
        fill={"type": "material", "id": "grid_material"},
    )


def _grid_safe_layer() -> AxialLayerSpec:
    """A spacer-grid layer represented as a derived lattice (pin through-path kept)."""
    return AxialLayerSpec(
        id="spacer_grid",
        name="spacer grid",
        z_min_cm=10.0,
        z_max_cm=10.5,
        fill={"type": "lattice", "id": "grid_lattice"},
        loading_id="grid_loading",
    )


# -- validator: the six required scenarios ----------------------------------


def test_scenario_1_2d_assembly_is_unaffected() -> None:
    """Scenario 1: ordinary 2D assembly, no axial signals -> no assembly3d issues."""
    plan = _assembly_plan()
    # No axial vocabulary in the requirement.
    issues = validate_assembly3d_plan(plan, requirement="17x17 PWR UO2 fuel assembly")
    codes = {issue.code for issue in issues}
    assert not any(code.startswith("assembly3d.") for code in codes)

    report = validate_simulation_plan(plan, requirement="17x17 PWR UO2 fuel assembly")
    assert not any(issue.code.startswith("assembly3d.") for issue in report.issues)
    # The 2D assembly path still reaches exportable via the renderer registry.
    _renderer, capability = choose_renderer(plan)
    assert capability.supported_renderer == "assembly"
    assert capability.renderability in {"exportable", "runnable"}


def test_scenario_2_axial_requirement_not_absorbed_by_2d_plan() -> None:
    """Scenario 2: axial requirement + 2D assembly plan -> axial_layers_required."""
    plan = _assembly_plan()
    requirement = "3D assembly with axial layers from 0 to 365 cm"
    issues = validate_assembly3d_plan(plan, requirement=requirement)
    codes = {issue.code for issue in issues}
    assert "assembly3d.axial_layers_required" in codes

    report = validate_simulation_plan(plan, requirement=requirement)
    assert report.is_valid is False
    assert any(issue.code == "assembly3d.axial_layers_required" for issue in report.issues)


def test_scenario_3_explicit_z_range_rejects_default_unit_slab() -> None:
    """Scenario 3: explicit z range + 2D plan -> default_z_extent_for_axial_problem."""
    plan = _assembly_plan()
    requirement = "spacer grid axial region from 10.0 cm to 13.8 cm"
    issues = validate_assembly3d_plan(plan, requirement=requirement)
    codes = {issue.code for issue in issues}
    assert "assembly3d.default_z_extent_for_axial_problem" in codes

    report = validate_simulation_plan(plan, requirement=requirement)
    assert report.is_valid is False


def test_scenario_4_spacer_grid_material_slab_blocked() -> None:
    """Scenario 4: spacer-grid layer filled with one material -> slab issue."""
    core = CoreSpec(
        id="core",
        name="core",
        lattice_id="active_lattice",
        boundary="reflective",
        axial_layers=[_grid_slab_layer()],
    )
    plan = _assembly_plan(core=core)
    requirement = "fuel assembly with spacer grid at z=10.0-13.8 cm"
    issues = validate_assembly3d_plan(plan, requirement=requirement)
    codes = {issue.code for issue in issues}
    assert "assembly3d.spacer_grid_material_slab" in codes

    # Plan validation must downgrade (not exportable).
    report = validate_simulation_plan(plan, requirement=requirement)
    assert any(issue.code == "assembly3d.spacer_grid_material_slab" for issue in report.issues)
    assert report.is_valid is False


def test_scenario_5_grid_layer_missing_pin_through_path() -> None:
    """Scenario 5: grid layer that drops pin/tube through-paths."""
    core = CoreSpec(
        id="core",
        name="core",
        lattice_id="active_lattice",
        boundary="reflective",
        axial_layers=[_grid_slab_layer()],
    )
    plan = _assembly_plan(core=core)
    requirement = "fuel pins, guide tubes, instrument tubes with spacer grid"
    issues = validate_assembly3d_plan(plan, requirement=requirement)
    codes = {issue.code for issue in issues}
    assert "assembly3d.pin_through_path_missing" in codes

    report = validate_simulation_plan(plan, requirement=requirement)
    assert any(issue.code == "assembly3d.pin_through_path_missing" for issue in report.issues)


def test_scenario_6_3d_assembly_with_axial_layers_not_misflagged() -> None:
    """Scenario 6: plan already carries axial_layers -> not flagged as missing them.

    A spacer-grid layer expressed as a derived lattice (loading_id present) is a
    safe through-path representation and must not trigger the slab/through-path
    issues either.
    """
    core = CoreSpec(
        id="core",
        name="core",
        lattice_id="active_lattice",
        boundary="mixed",
        axial_layers=[
            AxialLayerSpec(
                id="lower_active",
                name="lower active",
                z_min_cm=0.0,
                z_max_cm=10.0,
                fill={"type": "lattice", "id": "active_lattice"},
            ),
            _grid_safe_layer(),
            AxialLayerSpec(
                id="upper_active",
                name="upper active",
                z_min_cm=10.5,
                z_max_cm=20.0,
                fill={"type": "lattice", "id": "active_lattice"},
            ),
        ],
    )
    plan = _assembly_plan(core=core, grid_overlay=True)
    requirement = "3D assembly with axial layers"
    issues = validate_assembly3d_plan(plan, requirement=requirement)
    codes = {issue.code for issue in issues}
    assert "assembly3d.axial_layers_required" not in codes
    assert "assembly3d.default_z_extent_for_axial_problem" not in codes
    assert "assembly3d.spacer_grid_material_slab" not in codes
    assert "assembly3d.pin_through_path_missing" not in codes


# -- renderer-level defense (can_render still catches the slab) -------------


def test_renderer_can_render_catches_grid_slab() -> None:
    """The renderer registry still detects a grid slab even without a requirement."""
    core = CoreSpec(
        id="core",
        name="core",
        lattice_id="active_lattice",
        boundary="reflective",
        axial_layers=[_grid_slab_layer()],
    )
    plan = _assembly_plan(core=core)
    capability = RectAssemblyRenderer().can_render(plan)
    assert capability.renderability == "skeleton"
    assert any(
        issue.code == "assembly3d.spacer_grid_material_slab" for issue in capability.issues
    )
