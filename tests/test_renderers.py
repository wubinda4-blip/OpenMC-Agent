"""Tests for the pluggable renderer registry, skeleton fallback, and assembly checks."""

from pathlib import Path

import pytest

from openmc_agent.renderers import RENDERERS, choose_renderer, list_renderers
from openmc_agent.renderers.assembly import RectAssemblyRenderer
from openmc_agent.renderers.base import BaseRenderer
from openmc_agent.renderers.pin_cell import PinCellRenderer
from openmc_agent.renderers.skeleton import SkeletonRenderer
from openmc_agent.schemas import (
    AssemblySpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    CoreSpec,
    ExecutionCheckSpec,
    GeometrySpec,
    LatticeSpec,
    MaterialSpec,
    NuclideSpec,
    PinCellSpec,
    PlotSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
    SimulationPlan,
    SimulationSpec,
    SurfaceSpec,
    UniverseSpec,
)


def _pin_cell_plan() -> SimulationPlan:
    fuel = MaterialSpec(
        name="UO2 fuel",
        density_unit="g/cm3",
        density_value=10.4,
        composition=[NuclideSpec(name="U235", percent=4.95), NuclideSpec(name="O16", percent=2.0)],
    )
    moderator = MaterialSpec(
        name="Water",
        density_unit="g/cm3",
        density_value=1.0,
        composition=[NuclideSpec(name="H1", percent=2.0), NuclideSpec(name="O16", percent=1.0)],
    )
    spec = SimulationSpec(
        name="UO2 pin-cell",
        pin_cell=PinCellSpec(
            fuel=fuel,
            moderator=moderator,
            geometry=GeometrySpec(fuel_radius_cm=0.41, pitch_cm=1.26),
        ),
    )
    return SimulationPlan(
        model_spec=spec,
        plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="pin.png")],
        execution_check=ExecutionCheckSpec(
            settings=RunSettingsSpec(batches=5, inactive=1, particles=100)
        ),
    )


def _assembly_plan(*, materials, universe_pattern=None, shape=None, boundary="reflective") -> SimulationPlan:
    pattern = universe_pattern or [["pin", "pin"], ["pin", "pin"]]
    model = ComplexModelSpec(
        name="test assembly",
        kind="assembly",
        materials=materials,
        cells=[CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="pin", name="pin", cell_ids=["fuel_cell"])],
        lattices=[
            LatticeSpec(
                id="assembly_lattice",
                name="lattice",
                kind="rect",
                pitch_cm=(1.26, 1.26),
                shape=shape,
                universe_pattern=pattern,
            )
        ],
        assemblies=[
            AssemblySpec(
                id="assembly",
                name="root",
                lattice_id="assembly_lattice",
                boundary=boundary,
            )
        ],
        settings=RunSettingsSpec(batches=8, inactive=2, particles=80),
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


def _complete_fuel() -> ComplexMaterialSpec:
    return ComplexMaterialSpec(
        id="fuel",
        name="UO2 fuel",
        density_unit="g/cm3",
        density_value=10.4,
        composition=[NuclideSpec(name="U235", percent=4.95), NuclideSpec(name="O16", percent=2.0)],
    )


# -- registry plumbing ----------------------------------------------------


def test_registry_orders_skeleton_last() -> None:
    names = [renderer.name for renderer in list_renderers()]
    assert names[-1] == "skeleton"
    assert "pin_cell" in names and "assembly" in names


def test_all_renderers_implement_interface() -> None:
    for renderer in RENDERERS:
        assert isinstance(renderer, BaseRenderer)
        assert renderer.name
        assert hasattr(renderer, "can_render")
        assert hasattr(renderer, "render")


# -- pin cell backward compatibility --------------------------------------


def test_existing_pin_cell_still_works(tmp_path: Path) -> None:
    plan = _pin_cell_plan()
    renderer, capability = choose_renderer(plan)
    assert isinstance(renderer, PinCellRenderer)
    assert capability.renderability == "runnable"
    assert capability.is_executable is True

    result = renderer.render(plan, tmp_path)
    assert result.renderability == "runnable"
    assert "model.export_to_xml()" in result.script
    assert "openmc.run()" not in result.script
    assert (tmp_path / "model.py").exists()


# -- skeleton for incomplete assembly -------------------------------------


def test_skeleton_renderer_for_incomplete_assembly(tmp_path: Path) -> None:
    plan = _assembly_plan(
        materials=[
            ComplexMaterialSpec(
                id="fuel",
                name="UO2 fuel",
                chemical_formula="UO2",
                requires_human_confirmation=["density", "enrichment"],
            )
        ]
    )
    renderer, capability = choose_renderer(plan)
    # Either the assembly renderer's skeleton mode or the generic skeleton fallback is acceptable.
    assert renderer is not None
    assert capability.renderability == "skeleton"
    assert capability.is_executable is False

    result = renderer.render(plan, tmp_path)
    assert result.renderability == "skeleton"
    assert "NOT EXECUTABLE" in result.script
    assert "TODO" in result.script
    # Skeleton never calls export_to_xml or openmc.run.
    assert "openmc.run()" not in result.script
    assert (tmp_path / "model.py").exists()
    assert (tmp_path / "capability_report.json").exists()
    assert (tmp_path / "TODO.md").exists()


def test_skeleton_renderer_for_unrecognized_kind(tmp_path: Path) -> None:
    model = ComplexModelSpec(
        name="mixed thing",
        kind="mixed",
        materials=[_complete_fuel()],
        requires_human_confirmation=["unknown subsystem layout"],
    )
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=model,
        capability_report=RenderCapabilityReport(is_executable=False, supported_renderer="none"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(1.0, 1.0), filename="m.png")],
    )
    renderer, capability = choose_renderer(plan)
    assert isinstance(renderer, SkeletonRenderer)
    assert capability.renderability == "skeleton"
    result = renderer.render(plan, tmp_path)
    assert "NOT EXECUTABLE" in result.script


def test_core_renderer_rejects_mixed_percent_material_without_formula() -> None:
    model = ComplexModelSpec(
        name="bad core",
        kind="core",
        materials=[
            ComplexMaterialSpec(
                id="fuel",
                name="fuel",
                density_unit="g/cm3",
                density_value=10.0,
                composition=[
                    NuclideSpec(name="U235", percent=3.3, percent_type="wo"),
                    NuclideSpec(name="O16", percent=2.0, percent_type="ao"),
                ],
            )
        ],
        cells=[CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="pin", name="pin", cell_ids=["fuel_cell"])],
        lattices=[
            LatticeSpec(
                id="core_lattice",
                name="core lattice",
                kind="rect",
                pitch_cm=(1.26, 1.26),
                universe_pattern=[["pin"]],
            )
        ],
        core=CoreSpec(id="core", name="core", lattice_id="core_lattice"),
    )
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=model,
        capability_report=RenderCapabilityReport(is_executable=False, supported_renderer="none"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="core_xy.png")],
    )

    _renderer, capability = choose_renderer(plan)

    assert capability.renderability == "skeleton"
    assert capability.supported_renderer == "core"
    assert any("mixes atom and weight percents" in reason for reason in capability.reasons)


def test_core_renderer_can_render_auto_materialized_missing_cells() -> None:
    model = ComplexModelSpec(
        name="repairable missing cells core",
        kind="core",
        materials=[
            ComplexMaterialSpec(
                id="uo2",
                name="UO2",
                density_unit="g/cm3",
                density_value=10.0,
                chemical_formula="UO2",
                enrichment_percent=3.3,
            ),
            ComplexMaterialSpec(
                id="water",
                name="water",
                density_unit="g/cm3",
                density_value=0.997,
                chemical_formula="H2O",
            ),
        ],
        cells=[],
        universes=[
            UniverseSpec(id="pin_uo2", name="pin", cell_ids=["pin_uo2_fuel_cell", "pin_uo2_mod_cell"]),
            UniverseSpec(id="water_universe", name="water", cell_ids=["water_cell"]),
        ],
        lattices=[
            LatticeSpec(
                id="core_lattice",
                name="core lattice",
                kind="rect",
                pitch_cm=(1.26, 1.26),
                universe_pattern=[["pin_uo2", "water_universe"]],
            )
        ],
        core=CoreSpec(id="core", name="core", lattice_id="core_lattice"),
    )
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=model,
        capability_report=RenderCapabilityReport(is_executable=False, supported_renderer="none"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(2.52, 1.26), filename="core_xy.png")],
    )

    _renderer, capability = choose_renderer(plan)

    assert capability.supported_renderer == "core"
    assert capability.renderability != "skeleton"
    assert not any("references missing cells" in reason for reason in capability.reasons)


# -- assembly validation checks -------------------------------------------


def test_rect_assembly_lattice_shape_validation() -> None:
    plan = _assembly_plan(
        materials=[_complete_fuel()],
        universe_pattern=[["pin", "pin"], ["pin", "pin"]],
        shape=(15, 15),
    )
    renderer = RectAssemblyRenderer()
    capability = renderer.can_render(plan)
    assert capability.renderability != "runnable"
    assert any("shape" in reason and "15" in reason for reason in capability.reasons)


def test_rect_assembly_missing_lattice_pattern_becomes_skeleton(tmp_path: Path) -> None:
    model = ComplexModelSpec(
        name="incomplete assembly",
        kind="assembly",
        materials=[_complete_fuel()],
        cells=[CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="pin", name="pin", cell_ids=["fuel_cell"])],
        lattices=[
            LatticeSpec(
                id="assembly_lattice",
                name="lattice",
                kind="rect",
                pitch_cm=(1.26, 1.26),
                shape=(17, 17),
                universe_pattern=None,
            )
        ],
        assemblies=[AssemblySpec(id="assembly", name="root", lattice_id="assembly_lattice")],
    )
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=model,
        capability_report=RenderCapabilityReport(is_executable=False, supported_renderer="none"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(21.42, 21.42), filename="assembly.png")],
    )

    renderer = RectAssemblyRenderer()
    capability = renderer.can_render(plan)
    result = renderer.render(plan, tmp_path)

    assert capability.renderability == "skeleton"
    assert any("requires universe_pattern" in reason for reason in capability.reasons)
    assert "universe_pattern missing" in result.script
    assert "lattice assembly_lattice: rect lattice universe_pattern is missing" in (
        tmp_path / "TODO.md"
    ).read_text(encoding="utf-8")


def test_rect_assembly_missing_universe_reference() -> None:
    plan = _assembly_plan(
        materials=[_complete_fuel()],
        universe_pattern=[["pin", "ghost"], ["pin", "pin"]],
    )
    renderer = RectAssemblyRenderer()
    capability = renderer.can_render(plan)
    assert capability.renderability not in {"exportable", "runnable"}
    assert any("missing universes" in reason and "ghost" in reason for reason in capability.reasons)


def test_rect_assembly_material_completeness_missing_density() -> None:
    plan = _assembly_plan(
        materials=[
            ComplexMaterialSpec(
                id="fuel",
                name="UO2 fuel",
                chemical_formula="UO2",
                requires_human_confirmation=["density"],
            )
        ]
    )
    renderer = RectAssemblyRenderer()
    capability = renderer.can_render(plan)
    assert capability.is_executable is False
    assert capability.renderability == "skeleton"
    assert any("missing density" in reason for reason in capability.reasons)


def test_rect_assembly_material_completeness_missing_composition() -> None:
    plan = _assembly_plan(
        materials=[
            ComplexMaterialSpec(
                id="fuel",
                name="UO2 fuel",
                density_unit="g/cm3",
                density_value=10.4,
                requires_human_confirmation=["composition"],
            )
        ]
    )
    renderer = RectAssemblyRenderer()
    capability = renderer.can_render(plan)
    assert capability.renderability == "skeleton"
    assert any(
        "missing composition or chemical_formula" in reason for reason in capability.reasons
    )


def test_rect_assembly_complete_is_exportable_or_runnable() -> None:
    plan = _assembly_plan(materials=[_complete_fuel()])
    renderer, capability = choose_renderer(plan)
    assert isinstance(renderer, RectAssemblyRenderer)
    # Default execution_check settings (20 particles, 4 batches) are within smoke limits.
    assert capability.renderability in {"exportable", "runnable"}
    assert capability.is_executable is True


def test_rect_assembly_rejects_oversized_cylinder() -> None:
    plan = _assembly_plan(materials=[_complete_fuel()])
    plan.complex_model.surfaces = [
        SurfaceSpec(id="fuel_outer", kind="zcylinder", parameters={"r": 0.8}),
    ]
    renderer = RectAssemblyRenderer()
    capability = renderer.can_render(plan)
    # pitch=1.26 -> pitch/2=0.63; r=0.8 >= 0.63 violates the geometry constraint.
    assert capability.renderability != "runnable"
    assert any("pitch" in reason for reason in capability.reasons)


def test_rect_assembly_rejects_nonpositive_cylinder_radius() -> None:
    plan = _assembly_plan(materials=[_complete_fuel()])
    plan.complex_model.surfaces = [
        SurfaceSpec(id="fuel_outer", kind="zcylinder", parameters={"r": -0.1}),
    ]
    renderer = RectAssemblyRenderer()
    capability = renderer.can_render(plan)
    assert capability.renderability != "runnable"
    assert any("radius must be positive" in reason for reason in capability.reasons)


# -- renderer authoring stub ---------------------------------------------


def test_renderer_authoring_agent_reports_not_implemented() -> None:
    from openmc_agent.renderer_authoring import (
        AUTHORING_NOT_IMPLEMENTED,
        RendererAuthoringAgent,
    )

    plan = _pin_cell_plan()
    capability = RenderCapabilityReport(renderability="none", supported_renderer="none")
    candidate = RendererAuthoringAgent().propose_renderer(plan, capability)
    assert candidate.status == AUTHORING_NOT_IMPLEMENTED
    assert candidate.implemented is False
    # Safety policy is documented even though codegen is disabled.
    assert "subprocess" in candidate.safety_constraints.forbidden_modules
    assert any(
        call.startswith("subprocess") for call in candidate.safety_constraints.forbidden_calls
    )


def test_renderer_authoring_validator_rejects_forbidden_calls() -> None:
    from openmc_agent.renderer_authoring.validator import validate_renderer_source

    bad = "import os\nos.system('rm -rf /')\n"
    result = validate_renderer_source(bad)
    assert result.is_valid is False
    assert result.forbidden_modules_seen
