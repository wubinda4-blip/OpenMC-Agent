"""Tests that the executor faithfully renders composition without hidden modifications."""

from __future__ import annotations

from openmc_agent.executor import _render_material_definition
from openmc_agent.schemas import NuclideSpec, MaterialSpec


def _n(name, pct, pt="ao"):
    return NuclideSpec(name=name, percent=pct, percent_type=pt)


def test_executor_renders_composition_as_is():
    """Executor must not modify composition values."""
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 200.0)],
    )
    code = _render_material_definition(fuel, "mat")
    # O16 must appear with 200.0, not modified.
    assert "200.0" in code
    assert "U235" in code
    assert "U238" in code


def test_executor_renders_stoichiometric_as_is():
    """Executor should NOT correct stoichiometric O16=2 → 200."""
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
    )
    code = _render_material_definition(fuel, "mat")
    # O16 should appear with 2.0, NOT corrected to 200.
    assert "'O16', 2.0" in code


def test_executor_renders_boron_as_is():
    """Executor should NOT correct B10 ppm values."""
    water = MaterialSpec(
        name="water", density_unit="g/cm3", density_value=0.743,
        composition=[_n("B10", 0.001066), _n("H1", 0.666), _n("O16", 0.329)],
    )
    code = _render_material_definition(water, "mat")
    # B10 should appear with 0.001066, NOT corrected.
    assert "0.001066" in code


def test_executor_no_reconcile_functions_exist():
    """The _reconcile_* functions should no longer exist in executor."""
    import openmc_agent.executor as executor
    assert not hasattr(executor, "_reconcile_uo2_oxygen_scale")
    assert not hasattr(executor, "_reconcile_borated_water_boron")
