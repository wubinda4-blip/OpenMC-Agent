"""Tests for Axial Geometry human ambiguity routing."""

from openmc_agent.plan_builder.closed_loop.axial_geometry_issue_policy import axial_geometry_issue_owner


def test_reference_id_missing_not_human():
    """Missing reference is deterministic, not human ambiguity."""
    policy = axial_geometry_issue_owner("axial.universe_reference_missing")
    assert policy is not None
    assert "universes" in policy.owner_patch_types


def test_layer_overlap_not_human():
    """Layer overlap is deterministic."""
    policy = axial_geometry_issue_owner("axial.layer_overlap")
    assert policy is not None
    assert "axial_layers" in policy.owner_patch_types


def test_loading_unattached_not_human():
    """Loading unattached is deterministic."""
    policy = axial_geometry_issue_owner("axial.loading_unattached")
    assert policy is not None
    assert "axial_layers" in policy.owner_patch_types


def test_homogenization_policy_routed_through_facts():
    """Source homogenization ambiguity routes to facts as a source contract issue."""
    policy = axial_geometry_issue_owner("axial.domain_missing")
    assert policy is not None
    assert "facts" in policy.owner_patch_types
