"""Tests for Assembled Plan issue owner policy routing."""

from openmc_agent.plan_builder.closed_loop.assembled_plan_issue_policy import assembled_plan_issue_owner
from openmc_agent.plan_builder.closed_loop.models import PlanGateId


def test_source_strategy_unknown_routes_to_facts():
    policy = assembled_plan_issue_owner("assembled.source_strategy_unknown")
    assert policy is not None
    assert "facts" in policy.owner_patch_types
    assert PlanGateId.ASSEMBLED_PLAN in policy.gates_to_invalidate


def test_unresolved_reference_routes_to_materials():
    policy = assembled_plan_issue_owner("assembled.unresolved_reference")
    assert policy is not None
    assert "materials" in policy.owner_patch_types


def test_root_missing_routes_to_facts():
    policy = assembled_plan_issue_owner("assembled.root_missing")
    assert policy is not None
    assert "facts" in policy.owner_patch_types


def test_renderer_below_required_routes_to_axial():
    policy = assembled_plan_issue_owner("assembled.renderer_below_required_level")
    assert policy is not None
    assert PlanGateId.AXIAL_GEOMETRY in policy.gates_to_invalidate


def test_generic_assembled_code_returns_policy():
    policy = assembled_plan_issue_owner("assembled.some_new_code")
    assert policy is not None


def test_unknown_code_returns_none():
    policy = assembled_plan_issue_owner("unknown.code")
    assert policy is None
