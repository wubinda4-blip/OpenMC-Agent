"""Shared test fixtures for Phase-6 Assembled Plan Gate tests."""

from __future__ import annotations

from typing import Any

from openmc_agent.plan_builder.closed_loop.models import (
    PlanClosedLoopPolicy,
    PlanGateId,
    PlanStageState,
    PlanStageStatus,
)
from openmc_agent.plan_builder.closed_loop.controller import initialize_plan_loop_state
from openmc_agent.plan_builder.state import PlanBuildState
from openmc_agent.schemas import (
    CellSpec,
    ComplexModelSpec,
    ComplexMaterialSpec,
    ExecutionCheckSpec,
    LatticeSpec,
    NuclideSpec,
    PlotSpec,
    RegionSpec,
    SimulationPlan,
    SurfaceSpec,
    UniverseSpec,
    AssemblySpec,
    CoreSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
)


def make_assembled_plan(
    *,
    model_kind: str = "assembly",
    with_core: bool = False,
    with_materials: bool = True,
) -> SimulationPlan:
    """Build a minimal assembled SimulationPlan for testing."""
    def _nuclides(items):
        return [NuclideSpec(name=n, percent=p) for n, p in items]
    materials = [
        ComplexMaterialSpec(id="m_fuel", name="fuel", density_unit="g/cm3", density_value=10.0, composition=_nuclides([("U235", 0.03), ("U238", 0.97)])),
        ComplexMaterialSpec(id="m_clad", name="clad", density_unit="g/cm3", density_value=6.5, composition=_nuclides([("Zr", 1.0)])),
        ComplexMaterialSpec(id="m_water", name="water", density_unit="g/cm3", density_value=1.0, composition=_nuclides([("H1", 0.6667), ("O16", 0.3333)])),
    ] if with_materials else []
    surfaces = [SurfaceSpec(id="s1", kind="zcylinder", parameters={"r": 0.4})]
    regions = [RegionSpec(id="r1", expression="cylinder", surface_ids=["s1"])]
    cells = [CellSpec(id="c1", name="fuel_cell", region_id="r1", fill_type="material", fill_id="m_fuel")]
    universes = [UniverseSpec(id="u1", name="fuel_pin", cell_ids=["c1"])]
    lattice = LatticeSpec(id="lat1", name="fuel_lattice", kind="rect", pitch_cm=(1.26, 1.26), shape=(3, 3), universe_pattern=[["u1"] * 3] * 3)
    assembly = AssemblySpec(id="asm1", name="fuel_assembly", lattice_id="lat1")
    core = CoreSpec(id="core1", name="reactor_core", lattice_id=None, assembly_ids=[]) if with_core else None
    model = ComplexModelSpec(
        name="test_model",
        kind=model_kind,
        materials=materials,
        surfaces=surfaces,
        regions=regions,
        cells=cells,
        universes=universes,
        lattices=[lattice],
        lattice_loadings=[],
        assemblies=[assembly] if model_kind == "assembly" else [],
        core=core,
        settings=RunSettingsSpec(batches=10, inactive=5, particles=100, source_strategy="assembly_box"),
    )
    renderer_name = "core" if model_kind == "core" else "assembly"
    cap = RenderCapabilityReport(
        renderability="exportable", is_executable=True, supported_renderer=renderer_name,
        executable_subsystems=["materials", "cells", "universes", "rect_lattice", model_kind],
        unsupported_subsystems=[], reasons=[], warnings=[],
        required_human_confirmations=[], issues=[],
    )
    plot = PlotSpec(kind="slice", basis="xy", origin=[0.0, 0.0, 0.0], width_cm=[10.0, 10.0], filename="plot1")
    ec = ExecutionCheckSpec(enabled=True, settings={"batches": 5, "inactive": 2, "particles": 100})
    return SimulationPlan(
        schema_version="simulation_plan.v2",
        complex_model=model,
        capability_report=cap,
        plot_specs=[plot],
        execution_check=ec,
    )


def state_with_assembled_plan(
    *,
    plan: SimulationPlan | None = None,
    upstream_accepted: bool = True,
) -> PlanBuildState:
    state = PlanBuildState(state_id="assembled_test", requirement_text="test reactor")
    plan = plan or make_assembled_plan()
    state.assembled_plan = plan.model_dump(mode="json")
    policy = PlanClosedLoopPolicy(
        mode="controlled",
        gate_enabled={g: True for g in PlanGateId},
        assembled_plan_review_mode="controlled",
    )
    initialize_plan_loop_state(state, policy, ["facts", "materials", "universes", "axial_layers", "axial_overlays"])
    if upstream_accepted:
        for stage_key in ("plan_gate_facts", "plan_gate_material_universe", "plan_gate_placement", "plan_gate_axial_geometry"):
            stage = state.plan_loop_stages.get(stage_key)
            if stage is not None:
                stage.status = PlanStageStatus.ACCEPTED
                stage.metadata["accepted_input_hash"] = "upstream_hash_001"
    return state
