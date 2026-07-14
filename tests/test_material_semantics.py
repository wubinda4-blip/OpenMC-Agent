"""Tests for material semantics classification."""

from __future__ import annotations

from openmc_agent.material_semantics import (
    MATERIAL_ROLE_COOLANT,
    MATERIAL_ROLE_FUEL,
    classify_material_semantics,
    validate_material_semantics,
)
from openmc_agent.schemas import CompositionValueBasis, NuclideSpec, MaterialSpec


def _n(name, pct, pt="ao"):
    return NuclideSpec(name=name, percent=pct, percent_type=pt)


def test_uo2_stoichiometric_detected():
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
    )
    cls = classify_material_semantics(fuel)
    assert cls.is_uo2_like
    assert cls.has_stoichiometric_pattern
    assert cls.material_role == MATERIAL_ROLE_FUEL


def test_water_detected():
    water = MaterialSpec(
        name="coolant", density_unit="g/cm3", density_value=0.743,
        composition=[_n("H1", 0.666), _n("O16", 0.333)],
    )
    cls = classify_material_semantics(water)
    assert cls.is_water_like
    assert cls.material_role == MATERIAL_ROLE_COOLANT


def test_boron_ppm_pattern_detected():
    water = MaterialSpec(
        name="borated_water", density_unit="g/cm3", density_value=0.743,
        composition=[_n("B10", 0.001066), _n("H1", 0.666), _n("O16", 0.329)],
    )
    cls = classify_material_semantics(water)
    assert cls.has_ppm_pattern
    assert cls.is_water_like


def test_declared_basis_preserved():
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
        composition_basis=CompositionValueBasis.STOICHIOMETRIC_RATIO,
    )
    cls = classify_material_semantics(fuel)
    assert cls.declared_basis == CompositionValueBasis.STOICHIOMETRIC_RATIO
    assert cls.detected_basis == CompositionValueBasis.STOICHIOMETRIC_RATIO


def test_atom_density_basis_detected():
    fuel = MaterialSpec(
        name="fuel", density_unit="atom/b-cm", density_value=1e-3,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 200.0)],
    )
    cls = classify_material_semantics(fuel)
    assert cls.detected_basis == CompositionValueBasis.ATOM_DENSITY_BARN_CM


def test_weight_fraction_detected():
    steel = MaterialSpec(
        name="steel", density_unit="g/cm3", density_value=7.8,
        composition=[_n("Fe56", 70.0, "wo"), _n("Cr52", 18.0, "wo"), _n("Ni58", 12.0, "wo")],
    )
    cls = classify_material_semantics(steel)
    assert cls.detected_basis == CompositionValueBasis.WEIGHT_FRACTION


def test_unknown_basis_when_undeclared_and_ambiguous():
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
    )
    cls = classify_material_semantics(fuel)
    assert cls.detected_basis == CompositionValueBasis.UNKNOWN
    assert cls.ambiguity_reasons


def test_classification_does_not_modify_input():
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
    )
    original_o16 = fuel.composition[2].percent
    classify_material_semantics(fuel)
    assert fuel.composition[2].percent == original_o16


def test_validate_semantics_finds_stoichiometric_ambiguity():
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
    )
    issues = validate_material_semantics(fuel)
    codes = [i.code for i in issues]
    assert any("stoichiometric_ambiguous" in c for c in codes)
