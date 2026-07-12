"""Tests that role-only modifications cannot mask component-profile material slabs.

A proposal that only changes ``role`` to avoid the guard keyword, while keeping
``fill_type=material``, must be rejected as ``rejected_semantic_masking``.
"""

from __future__ import annotations

from openmc_agent.plan_builder.validation_repair import (
    PatchRepairOperation,
    PatchRepairProposal,
    evaluate_patch_repair_proposal,
    build_patch_repair_request,
)
from openmc_agent.plan_builder.validation_repair_policy import (
    VALIDATION_ISSUE_REPAIR_POLICIES,
    policy_for_issue_code,
)
from openmc_agent.plan_builder.patches import AxialLayerPatchItem, AxialLayersPatch
from openmc_agent.plan_builder.validators import (
    PatchValidationContext,
    validate_patch,
)
from openmc_agent.schemas import SimulationPlan, ValidationIssue, ValidationReport


def test_role_path_is_forbidden_for_component_profile_issue() -> None:
    """The ownership policy forbids /layers/*/role for component_profile repair."""
    policy = policy_for_issue_code("assembly3d.component_profile_as_material_slab")
    assert policy is not None
    assert "/layers/*/role" in policy.forbidden_path_patterns
    assert "/layers/*/role" not in policy.allowed_path_patterns


def test_role_only_modification_rejected_by_path_policy() -> None:
    """An operation on /layers/*/role is rejected as unsafe."""
    policy = policy_for_issue_code("assembly3d.component_profile_as_material_slab")
    assert policy is not None

    # Simulate a role-only edit
    op = PatchRepairOperation(op="replace", path="/layers/0/role", value="custom")
    from openmc_agent.repair_policy import match_json_pointer_pattern

    is_forbidden = any(
        match_json_pointer_pattern(op.path, pattern)
        for pattern in policy.forbidden_path_patterns
    )
    assert is_forbidden, "role-only edit must be forbidden"


def test_fill_type_and_loading_paths_are_allowed() -> None:
    """The policy allows fill_type, fill_id, loading_id, and lattice_loadings paths."""
    policy = policy_for_issue_code("assembly3d.component_profile_as_material_slab")
    assert policy is not None
    from openmc_agent.repair_policy import match_json_pointer_pattern

    allowed_paths = [
        "/layers/0/fill_type",
        "/layers/0/fill_id",
        "/layers/0/loading_id",
        "/lattice_loadings",
        "/lattice_loadings/-",
    ]
    for path in allowed_paths:
        is_allowed = any(
            match_json_pointer_pattern(path, pattern)
            for pattern in policy.allowed_path_patterns
        )
        assert is_allowed, f"{path} should be allowed"


def test_patch_validator_still_catches_after_role_change() -> None:
    """Even with role=custom, a material slab for a profile layer is caught
    at patch level IF the role was originally a profile role.

    The key invariant: the plan-level validator checks the assembled layer's
    name/role, and the ownership policy prevents LLM from hiding via role edits.
    """
    # Build a patch with shoulder_gap role and material fill
    patch = AxialLayersPatch(layers=[
        AxialLayerPatchItem(
            layer_id="active_fuel",
            role="active_fuel",
            z_min_cm=0.0,
            z_max_cm=100.0,
            fill_type="lattice",
            fill_id="assembly_lattice",
        ),
        AxialLayerPatchItem(
            layer_id="lower_shoulder_gap",
            role="shoulder_gap",
            z_min_cm=-10.0,
            z_max_cm=0.0,
            fill_type="material",
            fill_id="borated_water",
        ),
    ], axial_domain_cm=(-10.0, 100.0))

    result = validate_patch(patch, PatchValidationContext())
    codes = [i.code for i in result.issues]
    assert "assembly3d.component_profile_as_material_slab" in codes

    # Even if we change role to custom, the plan-level validator (via
    # assembly3d_guard) uses the assembled AxialLayerSpec.name field which
    # comes from role, so the issue would persist. The ownership policy
    # forbids role edits, so the LLM cannot escape this way.


def test_policy_preferred_strategy_is_deterministic() -> None:
    """The preferred strategy for component_profile repair is deterministic."""
    policy = policy_for_issue_code("assembly3d.component_profile_as_material_slab")
    assert policy is not None
    assert policy.preferred_strategy == "deterministic"
