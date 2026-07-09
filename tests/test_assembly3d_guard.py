"""Tests for the generic 3D-assembly / axial-geometry workflow guard.

These tests are intentionally benchmark-free: they exercise generic axial
signals (axial layers, spacer grids, explicit z ranges, nozzles) and confirm
the guard blocks 2D-assembly export when the requirement is genuinely 3D.
No VERA-specific facts are encoded anywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.openmc
openmc = pytest.importorskip(
    "openmc", reason="OpenMC is required for this integration test"
)

from openmc_agent.assembly3d_guard import (
    Assembly3DFeatureFlags,
    assembly3d_overlay_issues,
    detect_assembly_3d_features,
    validate_assembly3d_plan,
)
from openmc_agent.renderers import choose_renderer
from openmc_agent.renderers.assembly import RectAssemblyRenderer
from openmc_agent.schemas import (
    AssemblySpec,
    AxialLayerSpec,
    AxialOverlaySpec,
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


# -- Step 2: overlay IR + refined guard (VERA3 spacer-grid fix) ------------


def _fuel_region_with_grids_layer() -> AxialLayerSpec:
    """VERA3-like active fuel region: tall lattice layer whose purpose mentions
    embedded grids. This is NOT a spacer-grid slab; pins run through the whole
    365 cm lattice fill."""
    return AxialLayerSpec(
        id="layer_fuel_region",
        name="Fuel region with grids",
        z_min_cm=11.951,
        z_max_cm=377.711,
        fill={"type": "lattice", "id": "active_lattice"},
        purpose="Active fuel height with embedded grid axial sub-layers",
    )


def _core_with(layers: list[AxialLayerSpec], overlays: list[AxialOverlaySpec] | None = None) -> CoreSpec:
    return CoreSpec(
        id="core",
        name="core",
        lattice_id="active_lattice",
        boundary="mixed",
        axial_layers=layers,
        axial_overlays=overlays or [],
    )


def test_fuel_region_with_grids_not_misflagged_as_grid_slab() -> None:
    """A tall fuel-region lattice layer that merely mentions grids must not be
    treated as a spacer-grid slab. The earlier false positive
    (assembly3d.pin_through_path_missing on VERA3 layer_fuel_region) is gone.

    Because the requirement names spacer grids and the plan has no overlay, the
    requirement-aware ``spacer_grid_overlay_required`` code still fires (the
    grids are only a comment, not a real representation)."""
    core = _core_with([_fuel_region_with_grids_layer()])
    plan = _assembly_plan(core=core)
    requirement = "VERA 3D assembly with spacer grids, top/bottom nozzles, end plugs"
    issues = validate_assembly3d_plan(plan, requirement=requirement)
    codes = {issue.code for issue in issues}

    # The false positive is gone:
    assert "assembly3d.pin_through_path_missing" not in codes
    assert "assembly3d.spacer_grid_material_slab" not in codes
    # But the honest 'grids only mentioned, not represented' code fires:
    assert "assembly3d.spacer_grid_overlay_required" in codes

    # The renderer (requirement-agnostic) must NOT see a slab either.
    capability = RectAssemblyRenderer().can_render(plan)
    slab_codes = {
        issue.code for issue in capability.issues
        if issue.code in {"assembly3d.spacer_grid_material_slab", "assembly3d.pin_through_path_missing"}
    }
    assert slab_codes == set()


def test_lattice_fuel_layer_loading_id_none_not_flagged_for_through_path() -> None:
    """A normal lattice-filled fuel layer with loading_id=None must not trigger
    pin_through_path_missing: the lattice fill itself preserves pin through-paths."""
    core = _core_with([
        AxialLayerSpec(
            id="active",
            name="active fuel",
            z_min_cm=0.0,
            z_max_cm=365.0,
            fill={"type": "lattice", "id": "active_lattice"},
        ),
    ])
    plan = _assembly_plan(core=core)
    # No spacer-grid signal in requirement -> no overlayRequired either.
    issues = validate_assembly3d_plan(plan, requirement="17x17 PWR fuel assembly")
    codes = {issue.code for issue in issues}
    assert "assembly3d.pin_through_path_missing" not in codes
    assert "assembly3d.spacer_grid_material_slab" not in codes


def test_spacer_grid_skeleton_overlay_accepted_as_safe_downgrade() -> None:
    """A skeleton spacer_grid overlay is a valid safe representation: no geometric
    errors fire, only the review-only renderer-support downgrade."""
    overlay = AxialOverlaySpec(
        id="grid1",
        overlay_kind="spacer_grid",
        z_min_cm=20.0,
        z_max_cm=21.0,
        target_lattice_id="active_lattice",
        geometry_mode="skeleton",
        through_path_preserved=True,
        requires_human_confirmation=True,
    )
    core = _core_with([_fuel_region_with_grids_layer()], overlays=[overlay])
    plan = _assembly_plan(core=core)
    requirement = "VERA 3D assembly with spacer grids"
    issues = validate_assembly3d_plan(plan, requirement=requirement)
    codes = {issue.code for issue in issues}

    assert "assembly3d.spacer_grid_overlay_required" not in codes  # overlay present
    assert "assembly3d.spacer_grid_material_slab" not in codes
    assert "assembly3d.pin_through_path_missing" not in codes
    # Honest review-only downgrade (renderer has no overlay geometry yet):
    assert "assembly3d.axial_overlay_requires_renderer_support" in codes

    capability = RectAssemblyRenderer().can_render(plan)
    assert capability.renderability == "skeleton"


def test_homogenized_overlay_requires_renderer_support() -> None:
    """A non-skeleton overlay fidelity the renderer cannot produce triggers
    requires_renderer_support and keeps the model non-exportable."""
    overlay = AxialOverlaySpec(
        id="grid1",
        overlay_kind="spacer_grid",
        z_min_cm=20.0,
        z_max_cm=21.0,
        target_lattice_id="active_lattice",
        geometry_mode="homogenized_open_region",
        through_path_preserved=True,
    )
    core = _core_with([_fuel_region_with_grids_layer()], overlays=[overlay])
    plan = _assembly_plan(core=core)
    issues = validate_assembly3d_plan(plan, requirement="3D assembly with spacer grids")
    codes = {issue.code for issue in issues}

    assert "assembly3d.axial_overlay_requires_renderer_support" in codes
    capability = RectAssemblyRenderer().can_render(plan)
    assert capability.renderability == "skeleton"


def test_overlay_invalid_inverted_range() -> None:
    """An overlay whose z_min >= z_max triggers axial_overlay_invalid_range."""
    overlay = AxialOverlaySpec(
        id="grid_bad",
        overlay_kind="spacer_grid",
        z_min_cm=21.0,
        z_max_cm=20.0,
        target_lattice_id="active_lattice",
        geometry_mode="homogenized_open_region",
        through_path_preserved=True,
    )
    core = _core_with([_fuel_region_with_grids_layer()], overlays=[overlay])
    plan = _assembly_plan(core=core)
    issues = validate_assembly3d_plan(plan, requirement="3D assembly with spacer grids")
    codes = {issue.code for issue in issues}
    assert "assembly3d.axial_overlay_invalid_range" in codes


def test_overlay_domain_disjoint_range_is_invalid() -> None:
    """An overlay z-range entirely outside the axial-layer domain is invalid."""
    overlay = AxialOverlaySpec(
        id="grid_off",
        overlay_kind="spacer_grid",
        z_min_cm=500.0,
        z_max_cm=501.0,
        target_lattice_id="active_lattice",
        geometry_mode="homogenized_open_region",
        through_path_preserved=True,
    )
    # fuel region is z=11.951..377.711; overlay at 500..501 is disjoint.
    core = _core_with([_fuel_region_with_grids_layer()], overlays=[overlay])
    plan = _assembly_plan(core=core)
    issues = assembly3d_overlay_issues(plan.complex_model)
    codes = {issue.code for issue in issues}
    assert "assembly3d.axial_overlay_invalid_range" in codes


def test_overlay_missing_target_lattice() -> None:
    """A non-skeleton overlay whose target_lattice_id does not resolve triggers
    axial_overlay_missing_target."""
    overlay = AxialOverlaySpec(
        id="grid_notarget",
        overlay_kind="spacer_grid",
        z_min_cm=20.0,
        z_max_cm=21.0,
        target_lattice_id="does_not_exist",
        geometry_mode="homogenized_open_region",
        through_path_preserved=True,
    )
    core = _core_with([_fuel_region_with_grids_layer()], overlays=[overlay])
    plan = _assembly_plan(core=core)
    issues = validate_assembly3d_plan(plan, requirement="3D assembly with spacer grids")
    codes = {issue.code for issue in issues}
    assert "assembly3d.axial_overlay_missing_target" in codes


def test_overlay_without_through_path_evidence_flagged() -> None:
    """A non-skeleton spacer overlay that does not declare through_path_preserved
    (and no human confirmation) triggers pin_through_path_missing."""
    overlay = AxialOverlaySpec(
        id="grid_nothrough",
        overlay_kind="spacer_grid",
        z_min_cm=20.0,
        z_max_cm=21.0,
        target_lattice_id="active_lattice",
        geometry_mode="homogenized_open_region",
        through_path_preserved=None,
        requires_human_confirmation=False,
    )
    core = _core_with([_fuel_region_with_grids_layer()], overlays=[overlay])
    plan = _assembly_plan(core=core)
    issues = validate_assembly3d_plan(plan, requirement="3D assembly with spacer grids")
    codes = {issue.code for issue in issues}
    assert "assembly3d.pin_through_path_missing" in codes


# -- stale artifact persistence -------------------------------------------


def test_stale_render_artifacts_are_cleaned_and_marked(tmp_path) -> None:
    """A non-exportable run must overwrite a prior exportable run's model.py,
    XML and optimistic capability_report.json so the on-disk state matches the
    current run's conclusion."""
    from openmc_agent.graph import (
        _clean_stale_render_artifacts,
        _write_non_executable_marker,
    )
    from openmc_agent.schemas import ValidationIssue, ValidationReport

    # Simulate a prior exportable run's leftovers.
    (tmp_path / "model.py").write_text("model.export_to_xml()  # prior run", encoding="utf-8")
    (tmp_path / "geometry.xml").write_text("<prior/>", encoding="utf-8")
    (tmp_path / "materials.xml").write_text("<prior/>", encoding="utf-8")
    (tmp_path / "statepoint.1.h5").write_text("prior", encoding="utf-8")
    (tmp_path / "capability_report.json").write_text(
        '{"renderability":"exportable","is_executable":true}', encoding="utf-8"
    )
    # A run record that must be preserved.
    (tmp_path / "simulation_plan.json").write_text('{"prior":"plan"}', encoding="utf-8")

    _clean_stale_render_artifacts(tmp_path)

    # Regenerable render outputs gone; run records preserved.
    assert not (tmp_path / "model.py").exists()
    assert not (tmp_path / "geometry.xml").exists()
    assert not (tmp_path / "materials.xml").exists()
    assert not (tmp_path / "statepoint.1.h5").exists()
    assert not (tmp_path / "capability_report.json").exists()
    assert (tmp_path / "simulation_plan.json").exists()

    # The skipped run writes an honest NOT_EXECUTABLE marker.
    report = ValidationReport(
        is_valid=False,
        errors=["grid layer may truncate pin/tube geometry"],
        issues=[
            ValidationIssue(
                severity="error",
                code="assembly3d.pin_through_path_missing",
                message="grid layer may truncate pin/tube geometry",
            )
        ],
    )
    _write_non_executable_marker(tmp_path, report, plan=None)

    import json as _json
    sidecar = _json.loads((tmp_path / "capability_report.json").read_text(encoding="utf-8"))
    assert sidecar["renderability"] != "exportable"
    assert sidecar["is_executable"] is False
    todo = (tmp_path / "TODO.md").read_text(encoding="utf-8")
    assert "NOT_EXECUTABLE" in todo
