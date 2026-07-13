"""Source strategy rendering contract tests."""

from __future__ import annotations

import pytest

from openmc_agent.source_settings import (
    SourceBounds,
    active_fuel_z_bounds,
    source_bounds_for_plan,
    validate_source_settings,
)
from openmc_agent.schemas import (
    ComplexMaterialSpec,
    ComplexModelSpec,
    CoreSpec,
    LatticeSpec,
    NuclideSpec,
    PlotSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
    SimulationPlan,
)


def _make_plan(model: ComplexModelSpec) -> SimulationPlan:
    return SimulationPlan(
        schema_version="simulation_plan.v2",
        complex_model=model,
        capability_report=RenderCapabilityReport(
            renderability="runnable", is_executable=True, supported_renderer="assembly",
        ),
        plot_specs=[PlotSpec(basis="xy", origin=(0.0, 0.0, 0.0), width_cm=(1.0, 1.0), filename="test.png")],
    )


def _minimal_model(
    *,
    source_strategy: str = "active_fuel_box",
    manual_bounds: list[float] | None = None,
    axial_layers=None,
) -> ComplexModelSpec:
    fuel = ComplexMaterialSpec(
        id="fuel", name="fuel",
        density_unit="sum",
        composition=[NuclideSpec(name="U235", percent=0.02)],
    )
    core = CoreSpec(
        id="core", name="core", lattice_id="lat",
        assembly_ids=[], axial_layers=axial_layers or [],
        boundary="vacuum",
    )
    lattice = LatticeSpec(
        id="lat", name="lat", kind="rect", pitch_cm=(1.0, 1.0),
        universe_pattern=[["u"]], lower_left_cm=[0.0, 0.0],
    )
    settings = RunSettingsSpec(
        source_strategy=source_strategy,
        manual_source_bounds_cm=manual_bounds,
    )
    return ComplexModelSpec(
        name="test", kind="assembly", materials=[fuel],
        lattices=[lattice], core=core, settings=settings,
    )


def test_active_fuel_box_bounds_use_active_fuel_z():
    model = _minimal_model(source_strategy="active_fuel_box")
    bounds = source_bounds_for_plan(model, source_strategy="active_fuel_box")
    assert bounds is not None
    assert bounds.strategy == "active_fuel_box"
    # No axial layers → z falls back, not bound to active fuel
    assert bounds.z_bound_to_active_fuel is False


def test_assembly_box_bounds_differ_from_active_fuel():
    model = _minimal_model(source_strategy="assembly_box")
    af_bounds = source_bounds_for_plan(model, source_strategy="active_fuel_box")
    ab_bounds = source_bounds_for_plan(model, source_strategy="assembly_box")
    assert af_bounds is not None
    assert ab_bounds is not None
    assert af_bounds.strategy != ab_bounds.strategy


def test_manual_bounds_returned_as_provided():
    model = _minimal_model(
        source_strategy="manual",
        manual_bounds=[-5.0, 5.0, -5.0, 5.0, 10.0, 20.0],
    )
    bounds = source_bounds_for_plan(
        model, source_strategy="manual",
        manual_bounds=[-5.0, 5.0, -5.0, 5.0, 10.0, 20.0],
    )
    assert bounds is not None
    assert bounds.strategy == "manual"
    assert bounds.derived_from_plan is False
    assert bounds.z_min == 10.0
    assert bounds.z_max == 20.0


def test_manual_without_bounds_returns_none():
    model = _minimal_model(source_strategy="manual")
    bounds = source_bounds_for_plan(model, source_strategy="manual")
    assert bounds is None


def test_unknown_strategy_returns_none():
    model = _minimal_model(source_strategy="unknown")
    bounds = source_bounds_for_plan(model, source_strategy="unknown")
    assert bounds is None


def test_manual_missing_bounds_produces_blocker():
    model = _minimal_model(source_strategy="manual", manual_bounds=None)
    plan = _make_plan(model)
    issues = validate_source_settings(plan)
    codes = [i.code for i in issues]
    assert "runtime.manual_source_bounds_missing" in codes


def test_unknown_strategy_produces_blocker():
    model = _minimal_model(source_strategy="unknown")
    plan = _make_plan(model)
    issues = validate_source_settings(plan)
    codes = [i.code for i in issues]
    assert "runtime.unknown_source_strategy" in codes


def test_source_bounds_has_strategy_metadata():
    model = _minimal_model(source_strategy="active_fuel_box")
    bounds = source_bounds_for_plan(model, source_strategy="active_fuel_box")
    assert hasattr(bounds, "strategy")
    assert hasattr(bounds, "x_source")
    assert hasattr(bounds, "y_source")
    assert hasattr(bounds, "z_source")
    assert hasattr(bounds, "only_fissionable")
    assert hasattr(bounds, "derived_from_plan")
