"""Phase 3B: owner policy scope-aware fail-closed behaviour."""

from __future__ import annotations

import pytest

from openmc_agent.plan_builder.closed_loop.placement_issue_policy import placement_issue_owner, placement_owner_patch_types
from openmc_agent.plan_builder.closed_loop.retry_owner_policy import retry_owner_policy


def test_pin_map_and_assembly_catalog_never_selected_together_single_assembly() -> None:
    policy = retry_owner_policy("localized_insert.required_placement_missing", {"owner_patch_type": ""}, canonical_scope="single_assembly")
    assert policy is not None
    assert policy.owner_patch_types == ["pin_map"]
    assert "assembly_catalog" not in policy.owner_patch_types


def test_pin_map_and_assembly_catalog_never_selected_together_multi_assembly() -> None:
    policy = retry_owner_policy("localized_insert.required_placement_missing", {"owner_patch_type": ""}, canonical_scope="multi_assembly")
    assert policy is not None
    assert policy.owner_patch_types == ["assembly_catalog"]
    assert "pin_map" not in policy.owner_patch_types


def test_unknown_scope_fails_closed_for_placement() -> None:
    policy = retry_owner_policy("localized_insert.required_placement_missing", {"owner_patch_type": ""}, canonical_scope=None)
    assert policy is None  # fail closed when scope is unknown


def test_declared_owner_respected_within_correct_scope() -> None:
    policy = retry_owner_policy("localized_insert.anchor_mismatch", {"owner_patch_type": "pin_map"}, canonical_scope="single_assembly")
    assert policy is not None
    assert "pin_map" in policy.owner_patch_types


def test_declared_owner_wrong_scope_overridden_by_code_specific_resolution() -> None:
    # Declaring pin_map in multi_assembly scope is a conflict; the code-
    # specific resolution from placement_issue_policy overrides the declared
    # owner and returns the scope-correct type (assembly_catalog).
    policy = retry_owner_policy("localized_insert.required_placement_missing", {"owner_patch_type": "pin_map"}, canonical_scope="multi_assembly")
    assert policy is not None
    assert policy.owner_patch_types == ["assembly_catalog"]


def test_core_layout_owner_for_multiplicity_issue() -> None:
    policy = retry_owner_policy("localized_insert.core_multiplicity_mismatch", {}, canonical_scope="multi_assembly")
    assert policy is not None
    assert policy.owner_patch_types == ["core_layout"]


def test_placement_issue_policy_scope_aware_single_assembly() -> None:
    owners = placement_owner_patch_types("localized_insert.required_placement_missing", canonical_scope="single_assembly")
    assert owners == ["pin_map"]


def test_placement_issue_policy_scope_aware_multi_assembly() -> None:
    owners = placement_owner_patch_types("localized_insert.required_placement_missing", canonical_scope="multi_assembly")
    assert owners == ["assembly_catalog"]


def test_placement_issue_policy_unknown_scope_returns_all_for_revision_evaluator() -> None:
    # When scope is unknown, the revision evaluator needs both possible
    # owners so it can filter by what actually exists in state.
    owners = placement_owner_patch_types("localized_insert.required_placement_missing", canonical_scope=None)
    assert "pin_map" in owners
    assert "assembly_catalog" in owners


def test_materials_owner_registered_for_density_issue() -> None:
    policy = retry_owner_policy("materials.execution_density_required", {})
    assert policy is not None
    assert policy.owner_patch_types == ["materials"]


def test_universes_owner_registered_for_missing_universe() -> None:
    policy = retry_owner_policy("localized_insert.required_universe_missing", {})
    assert policy is not None
    assert policy.owner_patch_types == ["universes"]
