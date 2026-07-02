import pytest
from pydantic import ValidationError

from openmc_agent.schemas import (
    GeometrySpec,
    MaterialSpec,
    NuclideSpec,
    PinCellSpec,
    SettingsSpec,
    SimulationSpec,
)


def uo2_fuel() -> MaterialSpec:
    return MaterialSpec(
        name="UO2 fuel",
        density_unit="g/cm3",
        density_value=10.4,
        composition=[
            NuclideSpec(name="U235", percent=4.95),
            NuclideSpec(name="U238", percent=95.05),
            NuclideSpec(name="O16", percent=200.0),
        ],
    )


def water() -> MaterialSpec:
    return MaterialSpec(
        name="Water moderator",
        density_unit="g/cm3",
        density_value=1.0,
        composition=[
            NuclideSpec(name="H1", percent=2.0),
            NuclideSpec(name="O16", percent=1.0),
        ],
    )


def zircaloy() -> MaterialSpec:
    return MaterialSpec(
        name="Zircaloy cladding",
        density_unit="g/cm3",
        density_value=6.55,
        composition=[
            NuclideSpec(name="Zr", percent=1.0, kind="element"),
        ],
    )


def test_pin_cell_simulation_spec_validates() -> None:
    spec = SimulationSpec(
        name="UO2 pin-cell criticality",
        pin_cell=PinCellSpec(
            fuel=uo2_fuel(),
            moderator=water(),
            cladding=zircaloy(),
            geometry=GeometrySpec(
                fuel_radius_cm=0.41,
                clad_inner_radius_cm=0.42,
                clad_outer_radius_cm=0.48,
                pitch_cm=1.26,
            ),
        ),
        settings=SettingsSpec(batches=50, inactive=10, particles=1000),
    )

    assert spec.kind == "pin_cell"
    assert spec.settings.run_mode == "eigenvalue"
    assert spec.pin_cell.geometry.pitch_cm == 1.26


def test_fuel_radius_with_obvious_bad_size_fails_validation() -> None:
    with pytest.raises(ValidationError) as exc_info:
        GeometrySpec(fuel_radius_cm=10.0, pitch_cm=1.26)

    assert "fuel_radius_cm" in str(exc_info.value)


def test_cladding_outer_radius_must_exceed_inner_radius() -> None:
    with pytest.raises(ValidationError) as exc_info:
        GeometrySpec(
            fuel_radius_cm=0.41,
            clad_inner_radius_cm=0.48,
            clad_outer_radius_cm=0.42,
            pitch_cm=1.26,
        )

    assert "clad_outer_radius_cm" in str(exc_info.value)


def test_inactive_batches_must_be_less_than_total_batches() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SettingsSpec(batches=10, inactive=10, particles=1000)

    assert "inactive" in str(exc_info.value)
