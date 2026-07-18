"""Tests for the InvestigationPolicyRegistry."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.policy import (
    DEFAULT_INVESTIGATION_POLICIES,
    InvestigationPolicy,
    InvestigationPolicyRegistry,
    default_policy_registry,
)
from openmc_agent.plan_investigation.tool_registry import (
    TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE,
    TOOL_NAME_SEARCH_SOURCE_INDEX,
)


def test_default_registry_has_facts_materials_universes_axial_policies() -> None:
    reg = default_policy_registry()
    assert "facts" in reg.policies
    assert "materials" in reg.policies
    assert "universes" in reg.policies
    assert "axial_layers" in reg.policies


def test_facts_policy_recommends_search_and_structure_tools() -> None:
    reg = default_policy_registry()
    policy = reg.get("facts")
    assert TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE in policy.recommended_tools
    assert TOOL_NAME_SEARCH_SOURCE_INDEX in policy.recommended_tools


def test_facts_policy_search_terms_are_reactor_neutral() -> None:
    reg = default_policy_registry()
    policy = reg.get("facts")
    # Verify the expected scope-signal terms appear.
    for term in ("full core", "assembly", "lattice", "enrichment"):
        assert term in policy.recommended_search_terms


def test_materials_policy_search_terms() -> None:
    reg = default_policy_registry()
    policy = reg.get("materials")
    for term in ("material", "density", "composition"):
        assert term in policy.recommended_search_terms


def test_universes_policy_search_terms() -> None:
    reg = default_policy_registry()
    policy = reg.get("universes")
    for term in ("fuel pin", "guide tube", "RCCA", "Pyrex", "universe"):
        assert term in policy.recommended_search_terms


def test_axial_layers_policy_search_terms() -> None:
    reg = default_policy_registry()
    policy = reg.get("axial_layers")
    for term in ("spacer grid", "axial", "control rod"):
        assert term in policy.recommended_search_terms


def test_unknown_patch_type_returns_empty_policy() -> None:
    reg = default_policy_registry()
    policy = reg.get("nonexistent_patch_type")
    assert policy.recommended_tools == ()
    assert policy.recommended_search_terms == ()


def test_unknown_patch_type_suggestions_is_empty_list() -> None:
    reg = default_policy_registry()
    assert reg.suggestions_for("nonexistent") == []


def test_suggestions_render_as_list_of_strings() -> None:
    reg = default_policy_registry()
    suggestions = reg.suggestions_for("facts")
    assert isinstance(suggestions, list)
    assert all(isinstance(s, str) for s in suggestions)
    assert any("search_source_index" in s for s in suggestions)


def test_register_overrides_existing_policy() -> None:
    reg = InvestigationPolicyRegistry()
    reg.register(InvestigationPolicy(patch_type="facts", recommended_tools=("x",)))
    reg.register(InvestigationPolicy(patch_type="facts", recommended_tools=("y",)))
    assert reg.get("facts").recommended_tools == ("y",)


def test_default_policies_table_is_frozen() -> None:
    """The DEFAULT_INVESTIGATION_POLICIES table itself should not be
    mutated by callers; they should make their own registry.
    """
    keys_before = set(DEFAULT_INVESTIGATION_POLICIES.keys())
    reg = default_policy_registry()
    reg.register(
        InvestigationPolicy(patch_type="custom_patch", recommended_tools=("x",))
    )
    assert "custom_patch" not in DEFAULT_INVESTIGATION_POLICIES
    assert set(DEFAULT_INVESTIGATION_POLICIES.keys()) == keys_before


def test_policy_does_not_force_tool_choice() -> None:
    """Policy is advisory: an empty recommended_tools list is valid."""
    policy = InvestigationPolicy(patch_type="x", recommended_tools=())
    assert policy.render_suggestions() == []
