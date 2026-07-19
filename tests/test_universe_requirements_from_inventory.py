"""Tests for inventory-driven universe requirements."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.geometry_inventory import (
    GeometryComponentInventory,
    LocalizedInsertProfileBinding,
    MaterialRoleRequirement,
    RadialProfileRequirement,
)
from openmc_agent.plan_investigation.inventory_universe_requirements import (
    LEGACY_IMPLICIT_REQUIREMENT_IDS,
    InventoryUniverseRequirement,
    InventoryUniverseRequirementSet,
    compare_against_legacy_requirements,
    extract_universe_requirements_from_inventory,
)
from openmc_agent.plan_builder.material_requirements import (
    MaterialGenerationRequirement,
    MaterialGenerationRequirementSet,
)


def _inventory(profiles, role_reqs=None, bindings=None):
    return GeometryComponentInventory(
        inventory_id="inv_t",
        requirement_hash="rh",
        source_index_hash="sih",
        ledger_hash="lh",
        radial_profiles=tuple(profiles),
        material_role_requirements=tuple(role_reqs or []),
        localized_insert_profiles=tuple(bindings or []),
    )


def _fuel_profile(variant_id="v1"):
    return RadialProfileRequirement(
        profile_id=f"prof_fuel_{variant_id}",
        profile_kind="active_fuel_pin",
        component_kind="fuel_pin",
        fuel_variant_id=variant_id,
        required_cell_roles=("fuel",),
        required_material_roles=("fuel",),
    )


def test_one_requirement_per_inventory_profile() -> None:
    inv = _inventory([_fuel_profile("v1"), _fuel_profile("v2")])
    req_set = extract_universe_requirements_from_inventory(inv)
    assert len(req_set.requirements) == 2
    assert all(r.profile_kind == "active_fuel_pin" for r in req_set.requirements)


def test_each_requirement_bound_to_profile_id() -> None:
    inv = _inventory([_fuel_profile("v1")])
    req_set = extract_universe_requirements_from_inventory(inv)
    req = req_set.requirements[0]
    assert req.geometry_profile_id == "prof_fuel_v1"
    assert req.profile_kind == "active_fuel_pin"
    assert req.component_kind == "fuel_pin"


def test_no_implicit_end_plug_when_inventory_lacks_it() -> None:
    """The new path must NOT emit implicit:end_plug_* on its own."""
    inv = _inventory([_fuel_profile("v1")])
    req_set = extract_universe_requirements_from_inventory(inv)
    for req in req_set.requirements:
        assert "implicit:" not in req.requirement_id
        assert "end_plug" not in req.component_kind


def test_material_role_set_propagates_to_requirement() -> None:
    """Material requirements bound to a profile surface on the universe req."""
    profile = _fuel_profile("v1")
    inv = _inventory([profile])
    mreq_set = MaterialGenerationRequirementSet(
        requirements=(
            MaterialGenerationRequirement(
                requirement_id="mreq1",
                role="fuel",
                source_variant_id="v1",
                required_by_profile_ids=(profile.profile_id,),
            ),
        ),
    )
    req_set = extract_universe_requirements_from_inventory(
        inv, material_requirement_set=mreq_set
    )
    req = req_set.requirements[0]
    assert "fuel" in req.required_material_roles


def test_requirement_set_hash_deterministic() -> None:
    inv = _inventory([_fuel_profile("v1")])
    a = extract_universe_requirements_from_inventory(inv)
    b = extract_universe_requirements_from_inventory(inv)
    assert a.requirement_set_hash == b.requirement_set_hash


def test_requirement_set_hash_changes_with_inventory() -> None:
    inv_a = _inventory([_fuel_profile("v1")])
    inv_b = _inventory([_fuel_profile("v1"), _fuel_profile("v2")])
    a = extract_universe_requirements_from_inventory(inv_a)
    b = extract_universe_requirements_from_inventory(inv_b)
    assert a.requirement_set_hash != b.requirement_set_hash


def test_compare_flags_unsupported_implicit_requirements() -> None:
    inv = _inventory([_fuel_profile("v1")])
    req_set = extract_universe_requirements_from_inventory(inv)
    comparison = compare_against_legacy_requirements(
        inventory_requirements=req_set,
        legacy_requirement_ids=(
            "implicit:end_plug_lower",
            "implicit:end_plug_upper",
            "implicit:gas_gap",
            "implicit:water_pin",
        ),
    )
    # All four legacy ids are unsupported (no source evidence).
    assert len(comparison.unsupported_implicit_components) == 4
    assert any("implicit" in w for w in comparison.warnings)


def test_compare_flags_inventory_only_requirements() -> None:
    inv = _inventory([_fuel_profile("v1")])
    req_set = extract_universe_requirements_from_inventory(inv)
    comparison = compare_against_legacy_requirements(
        inventory_requirements=req_set,
        legacy_requirement_ids=(),  # legacy produced nothing
    )
    assert len(comparison.inventory_only_requirement_ids) == 1


def test_assumptions_never_allowed_by_default() -> None:
    inv = _inventory([_fuel_profile("v1")])
    req_set = extract_universe_requirements_from_inventory(inv)
    for req in req_set.requirements:
        assert req.assumptions_allowed is False


def test_legacy_implicit_requirement_ids_set_is_documented() -> None:
    """The known legacy implicit ids are exposed for advisory comparison."""
    assert "implicit:end_plug_lower" in LEGACY_IMPLICIT_REQUIREMENT_IDS
    assert "implicit:end_plug_upper" in LEGACY_IMPLICIT_REQUIREMENT_IDS
    assert "implicit:gas_gap" in LEGACY_IMPLICIT_REQUIREMENT_IDS
    assert "implicit:water_pin" in LEGACY_IMPLICIT_REQUIREMENT_IDS


def test_no_fabricated_geometry_values() -> None:
    """Inventory-driven requirements never carry numerical geometry values
    that weren't in the source (no invented radius / gap).
    """
    profile = _fuel_profile("v1")
    # Note: profile.radial_layers is empty by default — no fabricated radii.
    inv = _inventory([profile])
    req_set = extract_universe_requirements_from_inventory(inv)
    req = req_set.requirements[0]
    # No radial_layers → no fabricated r_min/r_max values.
    assert req.required_layer_roles == ()


def test_no_reactor_specific_branches() -> None:
    """Production code must not contain reactor-specific branches."""
    import ast
    import inspect
    from openmc_agent.plan_investigation import inventory_universe_requirements as mod

    src = inspect.getsource(mod)
    tree = ast.parse(src)
    forbidden = ("vera3", "vera4", "pwr_", "bwr_", "vver_", "htgr_")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value_lower = node.value.lower()
            for term in forbidden:
                assert term not in value_lower
