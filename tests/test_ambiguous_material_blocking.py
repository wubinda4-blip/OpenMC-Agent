"""Tests that ambiguous material compositions are blocked before rendering."""

from __future__ import annotations

from openmc_agent.material_normalization import normalize_material_semantics
from openmc_agent.material_validation import validate_normalized_material
from openmc_agent.schemas import (
    CompositionValueBasis,
    MaterialSpec,
    NormalizationStatus,
    NuclideSpec,
)


def _n(name, pct, pt="ao"):
    return NuclideSpec(name=name, percent=pct, percent_type=pt)


def test_ambiguous_uo2_blocked():
    """U sum≈100, O16≈2 without basis declaration → blocked."""
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
    )
    new_fuel, norm = normalize_material_semantics(fuel)
    assert norm.normalization_status == NormalizationStatus.AMBIGUOUS

    inv = validate_normalized_material(new_fuel)
    assert not inv.render_ready
    assert any("ambiguous" in b for b in inv.blockers)


def test_ambiguous_boron_blocked():
    """High B10 in water without basis declaration → blocked."""
    water = MaterialSpec(
        name="water", density_unit="g/cm3", density_value=0.743,
        composition=[_n("B10", 0.001066), _n("H1", 0.666), _n("O16", 0.329)],
    )
    new_water, norm = normalize_material_semantics(water)
    assert norm.normalization_status == NormalizationStatus.AMBIGUOUS

    inv = validate_normalized_material(new_water)
    assert not inv.render_ready


def test_explicit_basis_not_blocked():
    """Explicit stoichiometric_ratio basis → not ambiguous."""
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
        composition_basis=CompositionValueBasis.STOICHIOMETRIC_RATIO,
    )
    new_fuel, norm = normalize_material_semantics(fuel)
    assert norm.normalization_status == NormalizationStatus.DETERMINISTICALLY_NORMALIZED

    inv = validate_normalized_material(new_fuel)
    assert inv.render_ready


def test_human_confirmed_not_blocked():
    """Human-confirmed status → renderable."""
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
        composition_basis=CompositionValueBasis.ATOM_FRACTION,
        normalization_status=NormalizationStatus.HUMAN_CONFIRMED,
    )
    inv = validate_normalized_material(fuel)
    assert inv.render_ready


def test_blocked_material_preserves_original():
    """Ambiguous material should preserve original composition unchanged."""
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
    )
    normalize_material_semantics(fuel)
    assert fuel.composition[2].percent == 2.0
