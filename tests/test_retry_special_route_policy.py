"""Phase 8B Step 1: SpecialRetryRoute tests.

Tests:
1. Special route codes never return RetryOwnerPolicy.
2. SpecialRoute codes cannot be represented as owner_patch_types.
3. normalize_retry_request handles SpecialRetryRoute correctly.
4. Builder functions reject special routes.
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
    INVENTORY_HASH_MISMATCH,
    INVENTORY_SOURCE_CLAIM_MISSING,
)


def test_special_routes_are_not_retry_owner_policy() -> None:
    """Special route codes must NOT be RetryOwnerPolicy."""
    special_codes = [
        INVENTORY_SOURCE_CLAIM_MISSING,
        INVENTORY_HASH_MISMATCH,
        "inventory.source_span_invalid",
        "inventory.conflict_unresolved",
        "inventory.component_unresolved",
    ]
    for code in special_codes:
        policy = retry_owner_policy(code)
        assert isinstance(policy, SpecialRetryRoute), (
            f"{code}: expected SpecialRetryRoute, got {type(policy).__name__}"
        )


def test_special_route_no_owner_patch_types() -> None:
    """SpecialRetryRoute must NOT have owner_patch_types."""
    policy = retry_owner_policy(INVENTORY_SOURCE_CLAIM_MISSING)
    assert isinstance(policy, SpecialRetryRoute)
    assert not hasattr(policy, "owner_patch_types")


def test_retrieve_evidence_route_action() -> None:
    policy = retry_owner_policy(INVENTORY_SOURCE_CLAIM_MISSING)
    assert isinstance(policy, SpecialRetryRoute)
    assert policy.action == SpecialRetryAction.RETRIEVE_EVIDENCE


def test_inventory_rebuild_route_action() -> None:
    policy = retry_owner_policy(INVENTORY_HASH_MISMATCH)
    assert isinstance(policy, SpecialRetryRoute)
    assert policy.action == SpecialRetryAction.INVENTORY_REBUILD


def test_ask_human_route_action() -> None:
    policy = retry_owner_policy("inventory.conflict_unresolved")
    assert isinstance(policy, SpecialRetryRoute)
    assert policy.action == SpecialRetryAction.ASK_HUMAN
    assert policy.requires_human


def test_route_message_present() -> None:
    policy = retry_owner_policy(INVENTORY_HASH_MISMATCH)
    assert isinstance(policy, SpecialRetryRoute)
    assert len(policy.message) > 0
    assert "rebuild" in policy.message.lower()


def test_normalize_retry_request_accepts_special_routes() -> None:
    """normalize_retry_request should not reject special routes."""
    from openmc_agent.plan_builder.state import PlanBuildState

    state = PlanBuildState(state_id="test", requirement_text="test")
    from openmc_agent.plan_builder.closed_loop.retry_controller import (
        normalize_retry_request,
    )

    request = normalize_retry_request(
        {
            "code": INVENTORY_SOURCE_CLAIM_MISSING,
            "finding_ids": ["f1"],
        },
        state=state,
    )
    assert request is not None, "special route should not be rejected"
    assert request.reason_code == INVENTORY_SOURCE_CLAIM_MISSING
    assert request.repairable is False  # special routes are not repairable


def test_builder_rejects_special_routes() -> None:
    """Builder functions should return None for special routes."""
    from openmc_agent.plan_builder.state import PlanBuildState

    state = PlanBuildState(state_id="test", requirement_text="test")
    from openmc_agent.plan_builder.closed_loop.retry_request_builders import (
        build_retry_request_from_patch_validation,
    )

    result = build_retry_request_from_patch_validation(
        issue_code=INVENTORY_SOURCE_CLAIM_MISSING,
        patch_type="universes",
        state=state,
    )
    assert result is None, "builder should reject special route"
