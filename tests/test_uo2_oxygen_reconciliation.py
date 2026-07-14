"""Tests for UO2 oxygen scale and boron reconciliation in executor."""

from __future__ import annotations

from openmc_agent.executor import _reconcile_uo2_oxygen_scale, _reconcile_borated_water_boron
from openmc_agent.schemas import NuclideSpec


def _nuclide(name: str, percent: float, percent_type: str = "ao") -> NuclideSpec:
    return NuclideSpec(name=name, percent=percent, percent_type=percent_type)


# -- UO2 oxygen tests --


def test_uo2_oxygen_scaled_to_match_uranium():
    comp = [_nuclide("U235", 2.619), _nuclide("U238", 97.381), _nuclide("O16", 2.0)]
    result = _reconcile_uo2_oxygen_scale(comp)
    o16 = [c for c in result if c.name == "O16"][0]
    assert o16.percent == 200.0


def test_uo2_with_trace_isotopes():
    comp = [
        _nuclide("U234", 0.0219), _nuclide("U235", 2.619),
        _nuclide("U236", 0.012), _nuclide("U238", 97.3471),
        _nuclide("O16", 2.0),
    ]
    result = _reconcile_uo2_oxygen_scale(comp)
    o16 = [c for c in result if c.name == "O16"][0]
    assert o16.percent == 200.0


def test_already_correct_not_changed():
    comp = [_nuclide("U235", 2.619), _nuclide("U238", 97.381), _nuclide("O16", 200.0)]
    result = _reconcile_uo2_oxygen_scale(comp)
    o16 = [c for c in result if c.name == "O16"][0]
    assert o16.percent == 200.0


def test_non_uo2_material_not_changed():
    comp = [_nuclide("H1", 0.666), _nuclide("O16", 0.329), _nuclide("B10", 0.001)]
    result = _reconcile_uo2_oxygen_scale(comp)
    assert all(r.percent == c.percent for r, c in zip(result, comp))


def test_mixed_percent_types_not_changed():
    comp = [
        _nuclide("U235", 2.619, "ao"), _nuclide("U238", 97.381, "ao"),
        _nuclide("O16", 2.0, "wo"),
    ]
    result = _reconcile_uo2_oxygen_scale(comp)
    assert all(r.percent == c.percent for r, c in zip(result, comp))


def test_no_uranium_not_changed():
    comp = [_nuclide("Zr90", 50.0), _nuclide("Zr91", 30.0), _nuclide("O16", 2.0)]
    result = _reconcile_uo2_oxygen_scale(comp)
    assert all(r.percent == c.percent for r, c in zip(result, comp))


# -- Borated water boron tests --


def test_boron_ppm_corrected():
    """B10=0.001066 (encoding 1066 ppm) should be corrected to ~0.000118."""
    comp = [
        _nuclide("B10", 0.001066), _nuclide("B11", 0.004),
        _nuclide("H1", 0.666), _nuclide("O16", 0.329),
    ]
    result = _reconcile_borated_water_boron(comp, density_value=0.743, density_unit="g/cm3")
    b10 = [c for c in result if c.name == "B10"][0]
    assert b10.percent < 0.0005  # Should be well below original 0.001066


def test_boron_already_correct_not_changed():
    """B10=0.0001 (already small) should not be changed."""
    comp = [
        _nuclide("B10", 0.0001), _nuclide("H1", 0.666), _nuclide("O16", 0.329),
    ]
    result = _reconcile_borated_water_boron(comp, density_value=0.743, density_unit="g/cm3")
    b10 = [c for c in result if c.name == "B10"][0]
    assert b10.percent == 0.0001


def test_boron_non_water_not_changed():
    """Non-water material should not be affected."""
    comp = [
        _nuclide("B10", 0.001066), _nuclide("Fe56", 50.0), _nuclide("O16", 0.329),
    ]
    result = _reconcile_borated_water_boron(comp, density_value=7.8, density_unit="g/cm3")
    b10 = [c for c in result if c.name == "B10"][0]
    assert b10.percent == 0.001066


def test_boron_no_b10_not_changed():
    """Material without B10 should not be affected."""
    comp = [_nuclide("H1", 0.666), _nuclide("O16", 0.329)]
    result = _reconcile_borated_water_boron(comp, density_value=0.743, density_unit="g/cm3")
    assert all(r.percent == c.percent for r, c in zip(result, comp))


def test_boron_sum_density_not_changed():
    """Materials with 'sum' density should not be affected."""
    comp = [
        _nuclide("B10", 0.001066), _nuclide("H1", 0.666), _nuclide("O16", 0.329),
    ]
    result = _reconcile_borated_water_boron(comp, density_value=1.0, density_unit="sum")
    assert all(r.percent == c.percent for r, c in zip(result, comp))
