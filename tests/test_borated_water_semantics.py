"""Tests for borated water semantic normalization rules."""

from __future__ import annotations

import pytest

from openmc_agent.material_normalization import normalize_material_semantics
from openmc_agent.schemas import (
    CompositionValueBasis,
    ComplexMaterialSpec,
    MaterialSpec,
    NormalizationStatus,
    NuclideSpec,
)


def _n(name, pct, pt="ao"):
    return NuclideSpec(name=name, percent=pct, percent_type=pt)


class TestBoronPpmNormalization:
    """ppm_by_weight basis → deterministic atom fraction conversion."""

    def test_ppm_converted(self):
        water = MaterialSpec(
            name="borated_water", density_unit="g/cm3", density_value=0.743,
            composition=[
                _n("B10", 0.001066), _n("B11", 0.004),
                _n("H1", 0.666), _n("O16", 0.329),
            ],
            composition_basis=CompositionValueBasis.PPM_BY_WEIGHT,
        )
        new, result = normalize_material_semantics(water)
        assert result.normalization_status == NormalizationStatus.DETERMINISTICALLY_NORMALIZED
        b10 = [c for c in new.composition if c.name == "B10"][0]
        assert b10.percent < 0.0005
        assert b10.percent > 0.00001  # not zero

    def test_ppm_with_natural_split(self):
        water = MaterialSpec(
            name="borated_water", density_unit="g/cm3", density_value=0.743,
            composition=[
                _n("B10", 0.001066), _n("B11", 0.004),
                _n("H1", 0.666), _n("O16", 0.329),
            ],
            composition_basis=CompositionValueBasis.PPM_BY_WEIGHT,
        )
        new, _ = normalize_material_semantics(water)
        b10 = next(c for c in new.composition if c.name == "B10")
        b11 = next(c for c in new.composition if c.name == "B11")
        if b11.percent > 0:
            ratio = b10.percent / b11.percent
            # Natural B10/B11 ≈ 0.199/0.801 ≈ 0.249
            assert 0.1 < ratio < 0.5


class TestBoronAtomFractionPassthrough:
    """atom_fraction basis → not reinterpreted as ppm."""

    def test_atom_fraction_not_converted(self):
        water = MaterialSpec(
            name="coolant", density_unit="g/cm3", density_value=0.743,
            composition=[
                _n("B10", 0.0001),
                _n("H1", 0.666), _n("O16", 0.333),
            ],
            composition_basis=CompositionValueBasis.ATOM_FRACTION,
        )
        new, result = normalize_material_semantics(water)
        assert result.normalization_status == NormalizationStatus.NOT_REQUIRED
        b10 = [c for c in new.composition if c.name == "B10"][0]
        assert b10.percent == 0.0001  # unchanged


class TestBoronAmbiguousBlocking:
    """High B10 in water without declared basis → ambiguous."""

    def test_undeclared_boron_is_ambiguous(self):
        water = MaterialSpec(
            name="borated_water", density_unit="g/cm3", density_value=0.743,
            composition=[
                _n("B10", 0.001066),
                _n("H1", 0.666), _n("O16", 0.329),
            ],
        )
        _, result = normalize_material_semantics(water)
        assert result.normalization_status == NormalizationStatus.AMBIGUOUS
        assert result.requires_human_confirmation


class TestBoronComplexMaterialSpec:
    """ComplexMaterialSpec with boron."""

    def test_complex_ppm_normalized(self):
        mat = ComplexMaterialSpec(
            id="mat1", name="coolant", density_unit="g/cm3", density_value=0.743,
            composition=[
                _n("B10", 0.001066), _n("B11", 0.004),
                _n("H1", 0.666), _n("O16", 0.329),
            ],
            composition_basis=CompositionValueBasis.PPM_BY_WEIGHT,
        )
        new, result = normalize_material_semantics(mat)
        assert result.normalization_status == NormalizationStatus.DETERMINISTICALLY_NORMALIZED
        b10 = [c for c in new.composition if c.name == "B10"][0]
        assert b10.percent < 0.0005
