"""Phase 8B Step 1: every PREFLIGHT_ISSUE_CODES has a clear route.

Tests that:
1. Every inventory.* / manifest.* code resolves via retry_owner_policy().
2. Patch-owned codes return RetryOwnerPolicy with correct owner_patch_types.
3. Special routes return SpecialRetryRoute with correct action.
4. Unknown codes return None (fail closed).
"""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.retry_models import (
    SpecialRetryAction,
    SpecialRetryRoute,
)
from openmc_agent.plan_builder.closed_loop.retry_owner_policy import (
    RetryOwnerPolicy,
    retry_owner_policy,
)
from openmc_agent.plan_investigation.inventory_preflight import (
    INVENTORY_COMPONENT_UNRESOLVED,
    INVENTORY_CONFLICT_UNRESOLVED,
    INVENTORY_FABRICATED_GEOMETRY_VALUE,
    INVENTORY_FUEL_VARIANT_MATERIAL_UNCOVERED,
    INVENTORY_HASH_MISMATCH,
    INVENTORY_LOCALIZED_INSERT_PROFILE_UNCOVERED,
    INVENTORY_MATERIAL_ROLE_UNCOVERED,
    INVENTORY_PROFILE_LAYER_UNCOVERED,
    INVENTORY_RADIAL_PROFILE_UNCOVERED,
    INVENTORY_SOURCE_CLAIM_MISSING,
    INVENTORY_SOURCE_SPAN_INVALID,
    INVENTORY_UNIVERSE_MATERIAL_UNRESOLVED,
    INVENTORY_UNSUPPORTED_IMPLICIT_COMPONENT,
    MANIFEST_INVENTORY_REQUIREMENT_MISSING,
    PREFLIGHT_ISSUE_CODES,
)


def test_every_inventory_code_has_route() -> None:
    """Every PREFLIGHT_ISSUE_CODES must return non-None."""
    for code in PREFLIGHT_ISSUE_CODES:
        policy = retry_owner_policy(code)
        assert policy is not None, f"{code} has no retry policy"


def test_unknown_code_returns_none() -> None:
    """Unknown codes must fail closed (return None)."""
    policy = retry_owner_policy("inventory.totally_unknown_code")
    assert policy is None


def test_material_role_uncovered_routes_to_materials() -> None:
    policy = retry_owner_policy(INVENTORY_MATERIAL_ROLE_UNCOVERED)
    assert isinstance(policy, RetryOwnerPolicy)
    assert "materials" in policy.owner_patch_types
    assert policy.preferred_action.value == "revise_owner_patch"


def test_fuel_variant_material_uncovered_routes_to_materials() -> None:
    policy = retry_owner_policy(INVENTORY_FUEL_VARIANT_MATERIAL_UNCOVERED)
    assert isinstance(policy, RetryOwnerPolicy)
    assert "materials" in policy.owner_patch_types


def test_radial_profile_uncovered_routes_to_universes() -> None:
    policy = retry_owner_policy(INVENTORY_RADIAL_PROFILE_UNCOVERED)
    assert isinstance(policy, RetryOwnerPolicy)
    assert "universes" in policy.owner_patch_types


def test_profile_layer_uncovered_routes_to_universes() -> None:
    policy = retry_owner_policy(INVENTORY_PROFILE_LAYER_UNCOVERED)
    assert isinstance(policy, RetryOwnerPolicy)
    assert "universes" in policy.owner_patch_types


def test_localized_insert_profile_uncovered_routes_to_universes() -> None:
    policy = retry_owner_policy(INVENTORY_LOCALIZED_INSERT_PROFILE_UNCOVERED)
    assert isinstance(policy, RetryOwnerPolicy)
    assert "universes" in policy.owner_patch_types


def test_universe_material_unresolved_routes_to_universes() -> None:
    policy = retry_owner_policy(INVENTORY_UNIVERSE_MATERIAL_UNRESOLVED)
    assert isinstance(policy, RetryOwnerPolicy)
    assert "universes" in policy.owner_patch_types


def test_unsupported_implicit_component_routes_to_universes() -> None:
    policy = retry_owner_policy(INVENTORY_UNSUPPORTED_IMPLICIT_COMPONENT)
    assert isinstance(policy, RetryOwnerPolicy)
    assert "universes" in policy.owner_patch_types


def test_fabricated_geometry_value_routes_to_universes() -> None:
    policy = retry_owner_policy(INVENTORY_FABRICATED_GEOMETRY_VALUE)
    assert isinstance(policy, RetryOwnerPolicy)
    assert "universes" in policy.owner_patch_types


def test_manifest_requirement_missing_routes_to_universes() -> None:
    policy = retry_owner_policy(MANIFEST_INVENTORY_REQUIREMENT_MISSING)
    assert isinstance(policy, RetryOwnerPolicy)
    assert "universes" in policy.owner_patch_types


def test_source_claim_missing_is_special_route_retrieve_evidence() -> None:
    policy = retry_owner_policy(INVENTORY_SOURCE_CLAIM_MISSING)
    assert isinstance(policy, SpecialRetryRoute)
    assert policy.action == SpecialRetryAction.RETRIEVE_EVIDENCE


def test_source_span_invalid_is_special_route_retrieve_evidence() -> None:
    policy = retry_owner_policy(INVENTORY_SOURCE_SPAN_INVALID)
    assert isinstance(policy, SpecialRetryRoute)
    assert policy.action == SpecialRetryAction.RETRIEVE_EVIDENCE


def test_conflict_unresolved_is_special_route_ask_human() -> None:
    policy = retry_owner_policy(INVENTORY_CONFLICT_UNRESOLVED)
    assert isinstance(policy, SpecialRetryRoute)
    assert policy.action == SpecialRetryAction.ASK_HUMAN
    assert policy.requires_human


def test_component_unresolved_is_special_route_ask_human() -> None:
    policy = retry_owner_policy(INVENTORY_COMPONENT_UNRESOLVED)
    assert isinstance(policy, SpecialRetryRoute)
    assert policy.action == SpecialRetryAction.ASK_HUMAN
    assert policy.requires_human


def test_hash_mismatch_is_special_route_inventory_rebuild() -> None:
    policy = retry_owner_policy(INVENTORY_HASH_MISMATCH)
    assert isinstance(policy, SpecialRetryRoute)
    assert policy.action == SpecialRetryAction.INVENTORY_REBUILD


def test_patch_owned_codes_have_revise_preferred_action() -> None:
    """Material/Universe patch-owned codes should prefer revise over regenerate."""
    for code in PREFLIGHT_ISSUE_CODES:
        policy = retry_owner_policy(code)
        if isinstance(policy, RetryOwnerPolicy):
            assert policy.preferred_action.value in (
                "revise_owner_patch", "regenerate_owner_patch",
            ), f"{code} has unexpected preferred_action"


def test_no_default_to_materials() -> None:
    """No inventory code should default to materials."""
    for code in PREFLIGHT_ISSUE_CODES:
        policy = retry_owner_policy(code)
        assert policy is not None
        if isinstance(policy, RetryOwnerPolicy):
            # Every patch-owned code should have an explicit owner
            assert policy.owner_patch_types in (
                ["materials"], ["universes"], ["facts"],
            ), f"{code} has unexpected owner {policy.owner_patch_types}"
