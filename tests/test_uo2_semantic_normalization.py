"""Tests for UO2 semantic normalization rules."""

from __future__ import annotations

import pytest

from openmc_agent.material_normalization import normalize_material_semantics
from openmc_agent.material_semantics import classify_material_semantics
from openmc_agent.schemas import (
    CompositionValueBasis,
    ComplexMaterialSpec,
    MaterialSpec,
    NormalizationStatus,
    NuclideSpec,
)


def _n(name, pct, pt="ao"):
    return NuclideSpec(name=name, percent=pct, percent_type=pt)


class TestUO2StoichiometricNormalization:
    """Case B: U isotopic vector + O/U=2."""

    def test_simple_uo2(self):
        fuel = MaterialSpec(
            name="fuel", density_unit="g/cm3", density_value=10.0,
            composition=[_n("U235", 3.0), _n("U238", 97.0), _n("O16", 2.0)],
            composition_basis=CompositionValueBasis.STOICHIOMETRIC_RATIO,
        )
        new, result = normalize_material_semantics(fuel)
        assert result.normalization_status == NormalizationStatus.DETERMINISTICALLY_NORMALIZED
        o16 = [c for c in new.composition if c.name == "O16"][0]
        assert o16.percent == 200.0

    def test_uo2_with_trace_isotopes(self):
        fuel = MaterialSpec(
            name="fuel", density_unit="g/cm3", density_value=10.0,
            composition=[
                _n("U234", 0.02), _n("U235", 3.1),
                _n("U236", 0.01), _n("U238", 96.87),
                _n("O16", 2.0),
            ],
            composition_basis=CompositionValueBasis.STOICHIOMETRIC_RATIO,
        )
        new, _ = normalize_material_semantics(fuel)
        o16 = [c for c in new.composition if c.name == "O16"][0]
        assert o16.percent == pytest.approx(200.0, abs=0.1)

    def test_uo2_chemical_formula_basis(self):
        """chemical_formula='UO2' is a valid declaration for stoichiometric."""
        fuel = MaterialSpec(
            name="fuel", density_unit="g/cm3", density_value=10.0,
            composition=[_n("U235", 3.0), _n("U238", 97.0), _n("O16", 2.0)],
            composition_basis=CompositionValueBasis.STOICHIOMETRIC_RATIO,
            chemical_formula="UO2",
        )
        new, result = normalize_material_semantics(fuel)
        assert result.normalization_status == NormalizationStatus.DETERMINISTICALLY_NORMALIZED


class TestUO2AtomFractionPassthrough:
    """Case A: proper atom fractions should pass through unchanged."""

    def test_atom_fraction_not_modified(self):
        fuel = MaterialSpec(
            name="fuel", density_unit="g/cm3", density_value=10.0,
            composition=[_n("U235", 0.026), _n("U238", 0.974), _n("O16", 2.0)],
            composition_basis=CompositionValueBasis.ATOM_FRACTION,
        )
        new, result = normalize_material_semantics(fuel)
        assert result.normalization_status == NormalizationStatus.NOT_REQUIRED
        assert new.composition[2].percent == 2.0  # unchanged


class TestUO2AmbiguousBlocking:
    """No basis declaration → ambiguous, blocked."""

    def test_no_basis_is_ambiguous(self):
        fuel = MaterialSpec(
            name="fuel", density_unit="g/cm3", density_value=10.0,
            composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
        )
        _, result = normalize_material_semantics(fuel)
        assert result.normalization_status == NormalizationStatus.AMBIGUOUS
        assert result.requires_human_confirmation


class TestUO2AtomDensity:
    """Case C: atom density basis should pass through."""

    def test_atom_density_not_modified(self):
        fuel = MaterialSpec(
            name="fuel", density_unit="atom/b-cm", density_value=0.022,
            composition=[_n("U235", 5.7e-4), _n("U238", 2.2e-2), _n("O16", 4.6e-2)],
            composition_basis=CompositionValueBasis.ATOM_DENSITY_BARN_CM,
        )
        new, result = normalize_material_semantics(fuel)
        assert result.normalization_status == NormalizationStatus.NOT_REQUIRED


class TestUO2ComplexMaterialSpec:
    """All of the above, but for ComplexMaterialSpec."""

    def test_complex_stoichiometric(self):
        mat = ComplexMaterialSpec(
            id="mat1", name="fuel", density_unit="g/cm3", density_value=10.0,
            composition=[_n("U235", 3.0), _n("U238", 97.0), _n("O16", 2.0)],
            composition_basis=CompositionValueBasis.STOICHIOMETRIC_RATIO,
        )
        new, result = normalize_material_semantics(mat)
        assert result.normalization_status == NormalizationStatus.DETERMINISTICALLY_NORMALIZED
        o16 = [c for c in new.composition if c.name == "O16"][0]
        assert o16.percent == 200.0
