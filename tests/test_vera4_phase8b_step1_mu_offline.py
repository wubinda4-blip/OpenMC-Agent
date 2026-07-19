"""Phase 8B Step 1: VERA4 offline Material-Universe qualification tests.

These tests verify the complete registry + skeleton pipeline without
calling any LLM or running the real canary.  They are pure Python
deterministic checks that run in <1s.
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
    INVENTORY_FUEL_VARIANT_MATERIAL_UNCOVERED,
    INVENTORY_MATERIAL_ROLE_UNCOVERED,
    INVENTORY_RADIAL_PROFILE_UNCOVERED,
    MANIFEST_INVENTORY_REQUIREMENT_MISSING,
)


def test_vera4_material_role_uncovered_retryable() -> None:
    """VERA4: material_role_uncovered must be retryable via materials."""
    policy = retry_owner_policy(INVENTORY_MATERIAL_ROLE_UNCOVERED)
    assert isinstance(policy, RetryOwnerPolicy)
    assert policy.owner_patch_types == ["materials"]
    assert policy.preferred_action.value in ("revise_owner_patch",)


def test_vera4_fuel_variant_material_uncovered_retryable() -> None:
    """VERA4: fuel_variant_material_uncovered must be retryable via materials."""
    policy = retry_owner_policy(INVENTORY_FUEL_VARIANT_MATERIAL_UNCOVERED)
    assert isinstance(policy, RetryOwnerPolicy)
    assert policy.owner_patch_types == ["materials"]


def test_vera4_radial_profile_uncovered_retryable() -> None:
    """VERA4: radial_profile_uncovered must be retryable via universes."""
    policy = retry_owner_policy(INVENTORY_RADIAL_PROFILE_UNCOVERED)
    assert isinstance(policy, RetryOwnerPolicy)
    assert policy.owner_patch_types == ["universes"]


def test_vera4_manifest_requirement_missing_retryable() -> None:
    """VERA4: manifest.inventory_requirement_missing must be retryable via universes."""
    policy = retry_owner_policy(MANIFEST_INVENTORY_REQUIREMENT_MISSING)
    assert isinstance(policy, RetryOwnerPolicy)
    assert policy.owner_patch_types == ["universes"]


def test_vera4_findings_not_misrouted_to_research() -> None:
    """The four VERA4 findings must NOT route to RETRIEVE_EVIDENCE."""
    for code in [
        INVENTORY_MATERIAL_ROLE_UNCOVERED,
        INVENTORY_FUEL_VARIANT_MATERIAL_UNCOVERED,
        INVENTORY_RADIAL_PROFILE_UNCOVERED,
        MANIFEST_INVENTORY_REQUIREMENT_MISSING,
    ]:
        policy = retry_owner_policy(code)
        assert isinstance(policy, RetryOwnerPolicy), (
            f"{code}: expected RetryOwnerPolicy, not {type(policy).__name__}"
        )


def test_vera4_findings_no_fake_fallback() -> None:
    """Patch-owned codes must not have fallback as preferred action."""
    for code in [
        INVENTORY_MATERIAL_ROLE_UNCOVERED,
        INVENTORY_FUEL_VARIANT_MATERIAL_UNCOVERED,
        INVENTORY_RADIAL_PROFILE_UNCOVERED,
        MANIFEST_INVENTORY_REQUIREMENT_MISSING,
    ]:
        policy = retry_owner_policy(code)
        assert isinstance(policy, RetryOwnerPolicy)
        assert policy.preferred_action.value != "fail_closed"
