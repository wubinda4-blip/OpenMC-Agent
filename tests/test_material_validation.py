"""Tests for material validation and invariants."""

from __future__ import annotations

from openmc_agent.material_normalization import normalize_material_semantics
from openmc_agent.material_validation import (
    compute_material_invariants,
    validate_normalized_material,
)
from openmc_agent.schemas import (
    CompositionValueBasis,
    MaterialSpec,
    NormalizationStatus,
    NuclideSpec,
)


def _n(name, pct, pt="ao"):
    return NuclideSpec(name=name, percent=pct, percent_type=pt)


def test_uo2_invariants_after_normalization():
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
        composition_basis=CompositionValueBasis.STOICHIOMETRIC_RATIO,
    )
    new_fuel, _ = normalize_material_semantics(fuel)
    invariants = compute_material_invariants(new_fuel)
    checks = {c.name: c for c in invariants}
    assert checks["o_u_ratio"].passed
    assert checks["uranium_isotope_sum"].passed


def test_o_u_ratio_fails_without_normalization():
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
        composition_basis=CompositionValueBasis.ATOM_FRACTION,
    )
    invariants = compute_material_invariants(fuel)
    o_u_check = next(c for c in invariants if c.name == "o_u_ratio")
    assert not o_u_check.passed


def test_water_invariants():
    water = MaterialSpec(
        name="water", density_unit="g/cm3", density_value=0.743,
        composition=[_n("H1", 0.666), _n("O16", 0.333)],
        composition_basis=CompositionValueBasis.ATOM_FRACTION,
    )
    invariants = compute_material_invariants(water)
    h_o = next(c for c in invariants if c.name == "h_o_ratio")
    assert h_o.passed


def test_negative_fraction_detected():
    mat = MaterialSpec(
        name="bad", density_unit="g/cm3", density_value=1.0,
        composition=[_n("U235", 0.001), _n("U238", 99.999)],
        composition_basis=CompositionValueBasis.ATOM_FRACTION,
    )
    # Bypass Pydantic validation to simulate a negative fraction.
    mat.composition[0] = mat.composition[0].model_copy(update={"percent": -1.0})
    invariants = compute_material_invariants(mat)
    neg = next(c for c in invariants if c.name == "no_negative_fractions")
    assert not neg.passed


def test_duplicate_nuclides_detected():
    mat = MaterialSpec(
        name="bad", density_unit="g/cm3", density_value=1.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381)],
        composition_basis=CompositionValueBasis.ATOM_FRACTION,
    )
    # Simulate a duplicate by appending a copy.
    mat = mat.model_copy(update={
        "composition": [
            _n("U235", 2.619), _n("U235", 3.0), _n("U238", 97.381),
        ]
    })
    invariants = compute_material_invariants(mat)
    dup = next(c for c in invariants if c.name == "no_duplicate_nuclides")
    assert not dup.passed


def test_ambiguous_material_blocks_render():
    mat = MaterialSpec(
        name="mystery", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
        composition_basis=CompositionValueBasis.UNKNOWN,
        normalization_status=NormalizationStatus.AMBIGUOUS,
    )
    result = validate_normalized_material(mat)
    assert not result.render_ready
    assert result.blockers


def test_normalized_material_passes_validation():
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
        composition_basis=CompositionValueBasis.STOICHIOMETRIC_RATIO,
    )
    new_fuel, _ = normalize_material_semantics(fuel)
    result = validate_normalized_material(new_fuel)
    assert result.render_ready
