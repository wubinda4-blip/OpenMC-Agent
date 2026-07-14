"""Tests for UO2 oxygen scale and boron normalization (moved from executor).

These tests verify that the normalization module correctly handles
stoichiometric UO2 and ppm boron water when the basis is explicitly declared.
"""

from __future__ import annotations

from openmc_agent.material_normalization import normalize_material_semantics
from openmc_agent.schemas import CompositionValueBasis, MaterialSpec, NuclideSpec


def _nuclide(name: str, percent: float, percent_type: str = "ao") -> NuclideSpec:
    return NuclideSpec(name=name, percent=percent, percent_type=percent_type)


# -- UO2 stoichiometric normalization --


def test_uo2_stoichiometric_scaled_to_match_uranium():
    """UO2 with stoichiometric_ratio basis: O16=2 → O16=200."""
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[
            _nuclide("U235", 2.619),
            _nuclide("U238", 97.381),
            _nuclide("O16", 2.0),
        ],
        composition_basis=CompositionValueBasis.STOICHIOMETRIC_RATIO,
    )
    new_fuel, result = normalize_material_semantics(fuel)
    o16 = [c for c in new_fuel.composition if c.name == "O16"][0]
    assert o16.percent == 200.0


def test_uo2_with_trace_isotopes():
    comp = [
        _nuclide("U234", 0.0219), _nuclide("U235", 2.619),
        _nuclide("U236", 0.012), _nuclide("U238", 97.3471),
        _nuclide("O16", 2.0),
    ]
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=comp,
        composition_basis=CompositionValueBasis.STOICHIOMETRIC_RATIO,
    )
    new_fuel, _ = normalize_material_semantics(fuel)
    o16 = [c for c in new_fuel.composition if c.name == "O16"][0]
    assert o16.percent == 200.0


def test_atom_fraction_not_changed():
    """Already correct atom fractions (O16=200) should pass through."""
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[
            _nuclide("U235", 2.619),
            _nuclide("U238", 97.381),
            _nuclide("O16", 200.0),
        ],
        composition_basis=CompositionValueBasis.ATOM_FRACTION,
    )
    new_fuel, result = normalize_material_semantics(fuel)
    o16 = [c for c in new_fuel.composition if c.name == "O16"][0]
    assert o16.percent == 200.0
    assert result.normalization_status.value == "not_required"


def test_non_uo2_material_not_changed():
    fuel = MaterialSpec(
        name="water", density_unit="g/cm3", density_value=1.0,
        composition=[
            _nuclide("H1", 0.666), _nuclide("O16", 0.329), _nuclide("B10", 0.001),
        ],
        composition_basis=CompositionValueBasis.ATOM_FRACTION,
    )
    new_fuel, result = normalize_material_semantics(fuel)
    assert all(
        r.percent == c.percent
        for r, c in zip(new_fuel.composition, fuel.composition)
    )


def test_mixed_percent_types_not_changed():
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[
            _nuclide("U235", 2.619, "ao"), _nuclide("U238", 97.381, "ao"),
            _nuclide("O16", 2.0, "wo"),
        ],
        composition_basis=CompositionValueBasis.ATOM_FRACTION,
    )
    new_fuel, _ = normalize_material_semantics(fuel)
    assert all(
        r.percent == c.percent
        for r, c in zip(new_fuel.composition, fuel.composition)
    )


def test_no_uranium_not_changed():
    fuel = MaterialSpec(
        name="zro2", density_unit="g/cm3", density_value=6.5,
        composition=[
            _nuclide("Zr90", 50.0), _nuclide("Zr91", 30.0), _nuclide("O16", 2.0),
        ],
        composition_basis=CompositionValueBasis.ATOM_FRACTION,
    )
    new_fuel, _ = normalize_material_semantics(fuel)
    assert all(
        r.percent == c.percent
        for r, c in zip(new_fuel.composition, fuel.composition)
    )


# -- Borated water normalization --


def test_boron_ppm_normalized():
    """B10=0.001066 with ppm_by_weight basis should be normalized to ~0.000118."""
    water = MaterialSpec(
        name="borated_water", density_unit="g/cm3", density_value=0.743,
        composition=[
            _nuclide("B10", 0.001066), _nuclide("B11", 0.004),
            _nuclide("H1", 0.666), _nuclide("O16", 0.329),
        ],
        composition_basis=CompositionValueBasis.PPM_BY_WEIGHT,
    )
    new_water, result = normalize_material_semantics(water)
    b10 = [c for c in new_water.composition if c.name == "B10"][0]
    assert b10.percent < 0.0005


def test_boron_atom_fraction_not_changed():
    """B10=0.0001 with atom_fraction basis should not be changed."""
    water = MaterialSpec(
        name="water", density_unit="g/cm3", density_value=0.743,
        composition=[
            _nuclide("B10", 0.0001), _nuclide("H1", 0.666), _nuclide("O16", 0.329),
        ],
        composition_basis=CompositionValueBasis.ATOM_FRACTION,
    )
    new_water, _ = normalize_material_semantics(water)
    b10 = [c for c in new_water.composition if c.name == "B10"][0]
    assert b10.percent == 0.0001


def test_boron_non_water_not_normalized_as_ppm():
    """Non-water material with atom_fraction should not be ppm-normalized."""
    steel = MaterialSpec(
        name="steel", density_unit="g/cm3", density_value=7.8,
        composition=[
            _nuclide("B10", 0.001066), _nuclide("Fe56", 50.0), _nuclide("O16", 0.329),
        ],
        composition_basis=CompositionValueBasis.ATOM_FRACTION,
    )
    new_steel, _ = normalize_material_semantics(steel)
    b10 = [c for c in new_steel.composition if c.name == "B10"][0]
    assert b10.percent == 0.001066


def test_boron_no_b10_not_changed():
    water = MaterialSpec(
        name="water", density_unit="g/cm3", density_value=0.743,
        composition=[
            _nuclide("H1", 0.666), _nuclide("O16", 0.329),
        ],
        composition_basis=CompositionValueBasis.ATOM_FRACTION,
    )
    new_water, _ = normalize_material_semantics(water)
    assert all(
        r.percent == c.percent
        for r, c in zip(new_water.composition, water.composition)
    )
