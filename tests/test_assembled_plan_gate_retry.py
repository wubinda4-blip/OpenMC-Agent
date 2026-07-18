"""Tests for Assembled Plan retry request routing."""

from openmc_agent.plan_builder.closed_loop.retry_owner_policy import retry_owner_policy
from openmc_agent.plan_builder.closed_loop.models import PlanGateId


def test_retry_routes_assembled_source_code():
    policy = retry_owner_policy("assembled.source_strategy_unknown")
    assert policy is not None
    assert PlanGateId.ASSEMBLED_PLAN in policy.gates_to_invalidate


def test_retry_routes_assembled_reference_code():
    policy = retry_owner_policy("assembled.unresolved_reference")
    assert policy is not None


def test_retry_routes_assembled_root_code():
    policy = retry_owner_policy("assembled.root_missing")
    assert policy is not None
    assert "facts" in policy.owner_patch_types
