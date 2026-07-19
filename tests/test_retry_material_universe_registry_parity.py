"""Phase 8B Step 1: MU issue policy and retry_owner_policy agree.

Tests:
1. Every registered_material_universe_issue_codes() resolves.
2. material_universe_issue_owner and retry_owner_policy agree on owner.
3. No code returns None.
4. Facts-dependency codes route correctly.
"""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.material_universe_issue_policy import (
    material_universe_issue_owner,
    registered_material_universe_issue_codes,
)
from openmc_agent.plan_builder.closed_loop.retry_owner_policy import (
    RetryOwnerPolicy,
    retry_owner_policy,
)


def test_every_mu_code_has_retry_policy() -> None:
    """Every registered MU code must resolve to non-None."""
    for code in registered_material_universe_issue_codes():
        policy = retry_owner_policy(code)
        assert policy is not None, f"{code} has no retry policy"


def test_issue_policy_and_retry_owner_policy_agree() -> None:
    """Canonical MU issue policy and retry_owner_policy must agree."""
    for code in sorted(registered_material_universe_issue_codes()):
        mu_owner = material_universe_issue_owner(code)
        retry_policy = retry_owner_policy(code)
        assert retry_policy is not None, f"{code} has no retry policy"

        dep = mu_owner.get("dependency_patch_type")
        if dep == "facts":
            assert isinstance(retry_policy, RetryOwnerPolicy)
            assert "facts" in retry_policy.owner_patch_types, (
                f"{code}: expected facts owner, got {retry_policy.owner_patch_types}"
            )
            continue

        mu_owners = mu_owner.get("owner_patch_types", [])
        if not mu_owners:
            continue

        assert isinstance(retry_policy, RetryOwnerPolicy), (
            f"{code}: expected RetryOwnerPolicy, got {type(retry_policy).__name__}"
        )
        assert retry_policy.owner_patch_types == mu_owners, (
            f"{code}: expected owner {mu_owners}, got {retry_policy.owner_patch_types}"
        )


def test_mu_materials_owned_codes_have_correct_owner() -> None:
    """Materials-owned MU codes should route to materials."""
    materials_owned = {
        "material_universe.material_duplicate",
        "material_universe.material_density_invalid",
        "material_universe.transport_species_invalid",
        "material_universe.required_material_missing",
        "material_universe.required_fuel_variant_material_missing",
        "material_universe.placeholder_material_unresolved",
        "material_universe.compound_isotope_policy_missing",
        "material_universe.material_source_variant_unknown",
        "material_universe.material_provenance_missing",
        "material_universe.density_provenance_missing",
        "material_universe.materials_schema_invalid",
    }
    for code in materials_owned:
        policy = retry_owner_policy(code)
        assert isinstance(policy, RetryOwnerPolicy), f"{code}: expected RetryOwnerPolicy"
        assert "materials" in policy.owner_patch_types, (
            f"{code}: expected materials owner, got {policy.owner_patch_types}"
        )


def test_mu_universes_owned_codes_have_correct_owner() -> None:
    """Universes-owned MU codes should route to universes."""
    universes_owned = {
        "material_universe.material_reference_missing",
        "material_universe.material_role_mismatch",
        "material_universe.background_missing",
        "material_universe.fuel_cell_missing",
        "material_universe.guide_tube_wall_missing",
        "material_universe.guide_tube_moderator_missing",
        "material_universe.insert_material_missing",
        "material_universe.profile_material_structure_incomplete",
        "material_universe.universes_schema_invalid",
    }
    for code in universes_owned:
        policy = retry_owner_policy(code)
        assert isinstance(policy, RetryOwnerPolicy), f"{code}: expected RetryOwnerPolicy"
        assert "universes" in policy.owner_patch_types, (
            f"{code}: expected universes owner, got {policy.owner_patch_types}"
        )


def test_mu_facts_dependency_codes_route_to_facts() -> None:
    """Facts-dependency MU codes should route to facts."""
    facts_dep = {
        "material_universe.required_fuel_variant_missing",
    }
    for code in facts_dep:
        policy = retry_owner_policy(code)
        assert isinstance(policy, RetryOwnerPolicy), f"{code}: expected RetryOwnerPolicy"
        assert "facts" in policy.owner_patch_types, (
            f"{code}: expected facts owner, got {policy.owner_patch_types}"
        )


def test_unknown_mu_code_returns_none() -> None:
    """Unknown MU code must return None."""
    policy = retry_owner_policy("material_universe.totally_unknown")
    assert policy is None
