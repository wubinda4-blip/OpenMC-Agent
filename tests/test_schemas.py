import json

import pytest
from pydantic import ValidationError

from openmc_agent.schemas import (
    ExecutionCheckSpec,
    GeometrySpec,
    MaterialSpec,
    NuclideSpec,
    PinCellSpec,
    PlotSpec,
    RunSettingsSpec,
    SimulationPlan,
    SimulationSpec,
)


def test_uo2_material_validates_and_serializes_to_json() -> None:
    spec = MaterialSpec(
        name="UO2 fuel",
        density_unit="g/cm3",
        density_value=10.4,
        composition=[
            NuclideSpec(name="U235", percent=4.95, percent_type="ao"),
            NuclideSpec(name="U238", percent=95.05, percent_type="ao"),
            NuclideSpec(name="O16", percent=200.0, percent_type="ao"),
        ],
    )

    payload = json.loads(spec.model_dump_json())

    assert payload["name"] == "UO2 fuel"
    assert payload["density_unit"] == "g/cm3"
    assert payload["density_value"] == 10.4
    assert payload["composition"][0]["name"] == "U235"


def test_material_without_density_value_fails_validation() -> None:
    with pytest.raises(ValidationError) as exc_info:
        MaterialSpec(
            name="UO2 fuel",
            density_unit="g/cm3",
            composition=[NuclideSpec(name="U235", percent=1.0)],
        )

    assert "density_value" in str(exc_info.value)


def test_material_with_empty_composition_fails_validation() -> None:
    with pytest.raises(ValidationError) as exc_info:
        MaterialSpec(
            name="Water",
            density_unit="g/cm3",
            density_value=1.0,
            composition=[],
        )

    assert "composition" in str(exc_info.value)


def _uo2_pin_cell_simulation() -> SimulationSpec:
    fuel = MaterialSpec(
        name="UO2 fuel",
        density_unit="g/cm3",
        density_value=10.4,
        composition=[
            NuclideSpec(name="U235", percent=4.95),
            NuclideSpec(name="U238", percent=95.05),
            NuclideSpec(name="O16", percent=200.0),
        ],
    )
    moderator = MaterialSpec(
        name="Water moderator",
        density_unit="g/cm3",
        density_value=1.0,
        composition=[
            NuclideSpec(name="H1", percent=2.0),
            NuclideSpec(name="O16", percent=1.0),
        ],
    )
    return SimulationSpec(
        name="UO2 pin-cell criticality",
        pin_cell=PinCellSpec(
            fuel=fuel,
            moderator=moderator,
            geometry=GeometrySpec(fuel_radius_cm=0.41, pitch_cm=1.26),
        ),
        settings=RunSettingsSpec(batches=50, inactive=10, particles=1000),
    )


def test_simulation_plan_validates_and_serializes_plot_and_smoke_settings() -> None:
    plan = SimulationPlan(
        model_spec=_uo2_pin_cell_simulation(),
        plot_specs=[
            PlotSpec(
                basis="xy",
                origin=(0.0, 0.0, 0.0),
                width_cm=(1.26, 1.26),
                pixels=(500, 500),
                color_by="material",
                filename="pin_cell_xy.png",
                purpose="Check fuel/moderator geometry in the pin-cell midplane.",
            )
        ],
        execution_check=ExecutionCheckSpec(
            settings=RunSettingsSpec(batches=5, inactive=1, particles=100),
            expected_checks=["model exports XML", "smoke test starts without geometry errors"],
        ),
        expert_assumptions=["Reflective boundaries represent an infinite pin-cell."],
    )

    payload = json.loads(plan.model_dump_json())

    assert payload["schema_version"] == "simulation_plan.v1"
    assert payload["plot_specs"][0]["basis"] == "xy"
    assert payload["plot_specs"][0]["origin"] == [0.0, 0.0, 0.0]
    assert payload["plot_specs"][0]["width_cm"] == [1.26, 1.26]
    assert payload["execution_check"]["settings"]["particles"] == 100
    assert payload["expert_assumptions"] == [
        "Reflective boundaries represent an infinite pin-cell."
    ]


def test_plot_spec_rejects_invalid_basis() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PlotSpec(
            basis="abc",
            origin=(0.0, 0.0, 0.0),
            width_cm=(1.0, 1.0),
            pixels=(100, 100),
            filename="bad.png",
        )

    assert "basis" in str(exc_info.value)


def test_run_settings_rejects_invalid_smoke_test_counts() -> None:
    with pytest.raises(ValidationError) as exc_info:
        RunSettingsSpec(batches=5, inactive=1, particles=-1)

    assert "particles" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        RunSettingsSpec(batches=5, inactive=5, particles=100)

    assert "inactive" in str(exc_info.value)
