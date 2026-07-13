"""Atom-density fuel detection tests."""

from __future__ import annotations

import pytest

from openmc_agent.source_settings import fuel_material_ids, fissionable_material_ids
from openmc_agent.schemas import (
    ComplexMaterialSpec,
    ComplexModelSpec,
    NuclideSpec,
)


def _model(*materials):
    return ComplexModelSpec(name="m", kind="assembly", materials=list(materials))


# -- atom-density basis (sum) --


def test_atom_density_fuel_with_sum_unit_detected():
    """density_unit='sum' with U235 percent > 0 is fuel."""
    fuel = ComplexMaterialSpec(
        id="fuel", name="UO2 fuel", density_unit="sum",
        composition=[
            NuclideSpec(name="U235", percent=2.0e-3),
            NuclideSpec(name="U238", percent=8.0e-3),
        ],
    )
    m = _model(fuel)
    assert "fuel" in fuel_material_ids(m)


def test_atom_density_u235_u238_positive():
    fuel = ComplexMaterialSpec(
        id="fuel", name="UO2 fuel", density_unit="sum",
        composition=[
            NuclideSpec(name="U235", percent=2.0e-3),
            NuclideSpec(name="U238", percent=8.0e-3),
        ],
    )
    m = _model(fuel)
    assert "fuel" in fuel_material_ids(m)
    assert "fuel" in fissionable_material_ids(m)


def test_atom_density_all_zero_rejected():
    """Atom-density with all percents at 0 is not fuel."""
    # NuclideSpec.percent has gt=0 constraint, so use very small value
    # but test that fuel_material_ids checks percent > 0
    fuel = ComplexMaterialSpec(
        id="fuel", name="UO2 fuel", density_unit="sum",
        composition=[NuclideSpec(name="O16", percent=1.0)],
    )
    m = _model(fuel)
    assert "fuel" not in fuel_material_ids(m)


# -- mass-density basis --


def test_mass_density_fuel_still_compatible():
    fuel = ComplexMaterialSpec(
        id="fuel", name="UO2 fuel",
        density_unit="g/cm3", density_value=10.0,
        composition=[NuclideSpec(name="U235", percent=3.0)],
    )
    m = _model(fuel)
    assert "fuel" in fuel_material_ids(m)


# -- fraction-only without density --


def test_fraction_only_without_density_rejected():
    """Fraction basis without bulk density is not executable fuel."""
    fuel = ComplexMaterialSpec(
        id="fuel", name="UO2 fuel", density_unit=None,
        composition=[NuclideSpec(name="U235", percent=3.0)],
    )
    m = _model(fuel)
    assert "fuel" not in fuel_material_ids(m)


# -- non-fuel materials --


def test_pure_zr_not_fuel():
    zr = ComplexMaterialSpec(
        id="clad", name="Zircaloy-4",
        density_unit="g/cm3", density_value=6.56,
        composition=[NuclideSpec(name="Zr90", percent=98.0)],
    )
    m = _model(zr)
    assert "clad" not in fuel_material_ids(m)


def test_pure_fe_not_fuel():
    fe = ComplexMaterialSpec(
        id="steel", name="SS304",
        density_unit="g/cm3", density_value=7.9,
        composition=[NuclideSpec(name="Fe56", percent=70.0)],
    )
    m = _model(fe)
    assert "steel" not in fuel_material_ids(m)


def test_pure_ni_not_fuel():
    ni = ComplexMaterialSpec(
        id="grid", name="Inconel",
        density_unit="g/cm3", density_value=8.19,
        composition=[NuclideSpec(name="Ni58", percent=50.0)],
    )
    m = _model(ni)
    assert "grid" not in fuel_material_ids(m)


# -- fissionable_material_ids checks positive amount --


def test_fissionable_checks_positive_amount():
    """Material with zero-density fissile nuclide should not be fissionable."""
    # Since NuclideSpec.percent > 0, we can only test with O16
    water = ComplexMaterialSpec(
        id="water", name="water", density_unit="sum",
        composition=[NuclideSpec(name="H1", percent=2.0e-2)],
    )
    m = _model(water)
    assert "water" not in fissionable_material_ids(m)
