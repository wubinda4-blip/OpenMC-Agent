from openmc_agent.executor import render_openmc_script
from openmc_agent.schemas import (
    GeometrySpec,
    MaterialSpec,
    NuclideSpec,
    PinCellSpec,
    SettingsSpec,
    SimulationSpec,
)
from openmc_agent.validator import validate_openmc_script, validate_simulation_spec


def make_standard_spec() -> SimulationSpec:
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
        settings=SettingsSpec(batches=50, inactive=10, particles=1000),
    )


def test_validate_simulation_spec_accepts_standard_pin_cell() -> None:
    report = validate_simulation_spec(make_standard_spec())

    assert report.is_valid is True
    assert report.errors == []


def test_validate_simulation_spec_rejects_obvious_bad_fuel_radius() -> None:
    spec = make_standard_spec()
    spec.pin_cell.geometry = GeometrySpec.model_construct(
        fuel_radius_cm=10.0,
        pitch_cm=1.26,
        clad_inner_radius_cm=None,
        clad_outer_radius_cm=None,
    )

    report = validate_simulation_spec(spec)

    assert report.is_valid is False
    assert any("fuel_radius_cm" in error and "10.0" in error for error in report.errors)


def test_validate_openmc_script_requires_core_structures() -> None:
    report = validate_openmc_script("import openmc\nmodel.export_to_xml()\n")

    assert report.is_valid is False
    assert "materials" in " ".join(report.errors)
    assert "geometry" in " ".join(report.errors)
    assert "settings" in " ".join(report.errors)
    assert "tallies" in " ".join(report.errors)


def test_validate_openmc_script_accepts_rendered_script() -> None:
    script = render_openmc_script(make_standard_spec())

    report = validate_openmc_script(script, make_standard_spec())

    assert report.is_valid is True
    assert report.errors == []
