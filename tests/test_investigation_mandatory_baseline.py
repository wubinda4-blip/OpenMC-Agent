"""Tests for the deterministic investigation mandatory baseline."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.baseline import (
    BASELINE_POLICY_HASH_SEED,
    InvestigationBaselinePolicy,
    RequiredInvestigationAction,
    baseline_policy_for_patch_type,
    facts_baseline_policy,
    materials_baseline_policy,
    universes_baseline_policy,
)


def test_facts_baseline_requires_three_actions() -> None:
    policy = facts_baseline_policy()
    assert policy.patch_type == "facts"
    assert policy.action_count() == 3
    tool_names = set(policy.tool_names())
    assert "inspect_patch_schema" in tool_names
    assert "inspect_requirement_structure" in tool_names
    assert "search_source_index" in tool_names


def test_materials_baseline_queries_material_role_predicate() -> None:
    policy = materials_baseline_policy()
    assert policy.action_count() == 3
    query_action = next(
        a for a in policy.actions if a.tool_name == "query_evidence_ledger"
    )
    assert query_action.arguments["predicate"] == "material_role_required"


def test_universes_baseline_queries_geometry_profile_predicate() -> None:
    policy = universes_baseline_policy()
    query_action = next(
        a for a in policy.actions if a.tool_name == "query_evidence_ledger"
    )
    assert query_action.arguments["predicate"] == "geometry_profile_required"


def test_unknown_patch_type_returns_empty_policy() -> None:
    policy = baseline_policy_for_patch_type("pin_map")
    assert policy.action_count() == 0
    assert policy.actions == ()


def test_policy_hash_is_deterministic() -> None:
    a = facts_baseline_policy()
    b = facts_baseline_policy()
    assert a.policy_hash == b.policy_hash


def test_policy_hash_changes_with_actions() -> None:
    a = facts_baseline_policy()
    # Build a different policy by hand.
    action = RequiredInvestigationAction(
        action_id="",
        patch_type="facts",
        tool_name="search_source_index",
        arguments={"query": "different"},
        result_requirement="at_least_one",
    )
    b = InvestigationBaselinePolicy(patch_type="facts", actions=(action,))
    assert a.policy_hash != b.policy_hash


def test_action_hash_deterministic() -> None:
    a = RequiredInvestigationAction(
        action_id="",
        patch_type="facts",
        tool_name="inspect_patch_schema",
        arguments={"patch_type": "facts"},
    )
    b = RequiredInvestigationAction(
        action_id="",
        patch_type="facts",
        tool_name="inspect_patch_schema",
        arguments={"patch_type": "facts"},
    )
    assert a.action_hash == b.action_hash


def test_action_arguments_differ_produces_different_hash() -> None:
    a = RequiredInvestigationAction(
        action_id="",
        patch_type="facts",
        tool_name="search_source_index",
        arguments={"query": "alpha"},
    )
    b = RequiredInvestigationAction(
        action_id="",
        patch_type="facts",
        tool_name="search_source_index",
        arguments={"query": "beta"},
    )
    assert a.action_hash != b.action_hash


def test_materials_baseline_uses_inventory_roles_when_available() -> None:
    """When an inventory declares roles, the search query narrows to them."""

    class _StubInventory:
        declared_material_roles = ["fuel", "coolant"]

    policy = materials_baseline_policy(inventory=_StubInventory())
    search_action = next(
        a for a in policy.actions if a.tool_name == "search_source_index"
    )
    # First inventory-declared role is "fuel" → "fuel enrichment".
    assert search_action.arguments["query"] == "fuel enrichment"


def test_universes_baseline_uses_inventory_components_when_available() -> None:
    class _StubInventory:
        declared_component_kinds = ["fuel_pin", "guide_tube"]

    policy = universes_baseline_policy(inventory=_StubInventory())
    search_action = next(
        a for a in policy.actions if a.tool_name == "search_source_index"
    )
    assert search_action.arguments["query"] == "fuel pin"


def test_baseline_actions_include_no_reactor_specific_names() -> None:
    """No VERA / PWR / BWR specific names in any baseline action."""
    forbidden = ("VERA", "PWR", "BWR", "VVER", "HTGR", "SFR", "CANDU", "MOX", "Zircaloy")
    for patch_type in ("facts", "materials", "universes"):
        policy = baseline_policy_for_patch_type(patch_type)
        for action in policy.actions:
            for key, value in action.arguments.items():
                rendered = str(value)
                for term in forbidden:
                    assert term not in rendered, (
                        f"baseline {patch_type}.{key} mentions {term}"
                    )
