"""Phase 4: Material-Universe gate retry integration."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.material_universe_issue_policy import material_universe_issue_owner
from openmc_agent.plan_builder.closed_loop.retry_owner_policy import retry_owner_policy


def test_material_universe_density_issue_routes_to_materials_owner() -> None:
    policy = retry_owner_policy("material_universe.material_density_invalid")
    assert policy is not None
    assert policy.owner_patch_types == ["materials"]


def test_material_universe_reference_issue_routes_to_universes_owner() -> None:
    policy = retry_owner_policy("material_universe.material_reference_missing")
    assert policy is not None
    assert policy.owner_patch_types == ["universes"]


def test_material_universe_fuel_variant_missing_routes_to_facts() -> None:
    policy = retry_owner_policy("material_universe.required_fuel_variant_missing")
    assert policy is not None
    assert "facts" in policy.owner_patch_types


def test_issue_policy_and_retry_owner_policy_agree() -> None:
    """The Phase-4 issue policy and Phase-3B retry owner policy must agree."""
    codes = [
        "material_universe.material_density_invalid",
        "material_universe.material_reference_missing",
        "material_universe.required_fuel_variant_missing",
    ]
    for code in codes:
        mu_owner = material_universe_issue_owner(code)
        retry_policy = retry_owner_policy(code)
        assert retry_policy is not None
        if mu_owner.get("dependency_patch_type") == "facts":
            assert "facts" in retry_policy.owner_patch_types
        elif mu_owner.get("owner_patch_types"):
            assert retry_policy.owner_patch_types == mu_owner["owner_patch_types"]
