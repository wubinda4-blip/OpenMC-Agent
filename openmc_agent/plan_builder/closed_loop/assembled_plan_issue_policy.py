"""Deterministic owner policy for Assembled Plan Gate issue codes."""

from __future__ import annotations

from typing import Any

from .models import PlanGateId
from .retry_models import PlanRetryAction
from .retry_owner_policy import RetryOwnerPolicy


_FACTS_DEPENDENCY_CODES = {
    "assembled.source_strategy_unknown",
    "assembled.required_object_missing",
}

_MATERIALS_DEPENDENCY_CODES = {
    "assembled.unresolved_reference",
}

_UNIVERSES_DEPENDENCY_CODES = {
    "assembled.required_universe_unreachable",
    "assembled.required_material_unreachable",
}

_PLACEMENT_DEPENDENCY_CODES = {
    "assembled.localized_insert_unreachable",
}

_AXIAL_LAYERS_CODES = {
    "assembled.required_lattice_unreachable",
    "assembled.required_loading_unreachable",
    "assembled.required_axial_layer_unreachable",
}

_AXIAL_OVERLAYS_CODES = {
    "assembled.required_overlay_unreachable",
    "assembled.grid_geometry_unreachable",
}

_ROOT_CODES = {
    "assembled.root_missing",
    "assembled.root_ambiguous",
    "assembled.root_kind_mismatch",
    "assembled.root_lattice_missing",
    "assembled.root_assembly_missing",
    "assembled.root_axial_structure_missing",
    "assembled.reference_cycle",
    "assembled.root_cycle",
}

_RENDERER_CODES = {
    "assembled.renderer_none",
    "assembled.renderer_skeleton_only",
    "assembled.renderer_below_required_level",
    "assembled.renderer_selection_mismatch",
    "assembled.capability_report_stale",
    "assembled.capability_report_inconsistent",
    "assembled.unsupported_required_subsystem",
    "assembled.required_human_confirmation_open",
}


def assembled_plan_issue_owner(code: str, issue: dict[str, Any] | None = None) -> RetryOwnerPolicy | None:
    """Return the deterministic owner policy for an assembled plan issue code."""
    if code in _FACTS_DEPENDENCY_CODES:
        return RetryOwnerPolicy(
            owner_patch_types=["facts"], preferred_action=PlanRetryAction.REVISE_OWNER_PATCH,
            fallback_action=PlanRetryAction.REGENERATE_OWNER_PATCH,
            gates_to_invalidate=[PlanGateId.FACTS, PlanGateId.MATERIAL_UNIVERSE, PlanGateId.PLACEMENT, PlanGateId.AXIAL_GEOMETRY, PlanGateId.ASSEMBLED_PLAN],
            required_acceptance_checks=["facts_schema", "facts_consistency", "canonical_task_plan"],
        )
    if code in _MATERIALS_DEPENDENCY_CODES:
        return RetryOwnerPolicy(
            owner_patch_types=["materials"], preferred_action=PlanRetryAction.REVISE_OWNER_PATCH,
            fallback_action=PlanRetryAction.REGENERATE_OWNER_PATCH,
            gates_to_invalidate=[PlanGateId.MATERIAL_UNIVERSE, PlanGateId.PLACEMENT, PlanGateId.AXIAL_GEOMETRY, PlanGateId.ASSEMBLED_PLAN],
            required_acceptance_checks=["materials_schema", "material_species", "density_policy"],
        )
    if code in _UNIVERSES_DEPENDENCY_CODES:
        return RetryOwnerPolicy(
            owner_patch_types=["universes"], preferred_action=PlanRetryAction.REGENERATE_OWNER_PATCH,
            gates_to_invalidate=[PlanGateId.MATERIAL_UNIVERSE, PlanGateId.PLACEMENT, PlanGateId.AXIAL_GEOMETRY, PlanGateId.ASSEMBLED_PLAN],
            required_acceptance_checks=["universes_schema", "material_references", "required_universe_ids"],
        )
    if code in _PLACEMENT_DEPENDENCY_CODES:
        return RetryOwnerPolicy(
            owner_patch_types=["localized_insert_profiles"], preferred_action=PlanRetryAction.REVISE_OWNER_PATCH,
            fallback_action=PlanRetryAction.REGENERATE_OWNER_PATCH,
            gates_to_invalidate=[PlanGateId.PLACEMENT, PlanGateId.AXIAL_GEOMETRY, PlanGateId.ASSEMBLED_PLAN],
            required_acceptance_checks=["placement_preflight", "placement_critic"],
        )
    if code in _AXIAL_LAYERS_CODES:
        return RetryOwnerPolicy(
            owner_patch_types=["axial_layers"], preferred_action=PlanRetryAction.REGENERATE_OWNER_PATCH,
            gates_to_invalidate=[PlanGateId.AXIAL_GEOMETRY, PlanGateId.ASSEMBLED_PLAN],
            required_acceptance_checks=["patch_schema", "patch_validation", "axial_preflight"],
        )
    if code in _AXIAL_OVERLAYS_CODES:
        return RetryOwnerPolicy(
            owner_patch_types=["axial_overlays"], preferred_action=PlanRetryAction.REGENERATE_OWNER_PATCH,
            gates_to_invalidate=[PlanGateId.AXIAL_GEOMETRY, PlanGateId.ASSEMBLED_PLAN],
            required_acceptance_checks=["patch_schema", "patch_validation", "axial_preflight"],
        )
    if code in _ROOT_CODES:
        # Root issues typically require fixing the model structure.
        # Default to facts as the upstream owner.
        return RetryOwnerPolicy(
            owner_patch_types=["facts"], preferred_action=PlanRetryAction.REVISE_OWNER_PATCH,
            fallback_action=PlanRetryAction.REGENERATE_OWNER_PATCH,
            gates_to_invalidate=[PlanGateId.FACTS, PlanGateId.MATERIAL_UNIVERSE, PlanGateId.PLACEMENT, PlanGateId.AXIAL_GEOMETRY, PlanGateId.ASSEMBLED_PLAN],
            required_acceptance_checks=["facts_schema", "canonical_task_plan"],
        )
    if code in _RENDERER_CODES:
        # Renderer issues require fixing the model structure (upstream patches).
        return RetryOwnerPolicy(
            owner_patch_types=["axial_layers", "axial_overlays"], preferred_action=PlanRetryAction.REGENERATE_OWNER_PATCH,
            gates_to_invalidate=[PlanGateId.AXIAL_GEOMETRY, PlanGateId.ASSEMBLED_PLAN],
            required_acceptance_checks=["patch_schema", "patch_validation", "axial_preflight"],
        )
    if code.startswith("assembled."):
        # Generic fallback: route to facts (the upstream contract owner).
        return RetryOwnerPolicy(
            owner_patch_types=["facts"], preferred_action=PlanRetryAction.REVISE_OWNER_PATCH,
            fallback_action=PlanRetryAction.REGENERATE_OWNER_PATCH,
            gates_to_invalidate=[PlanGateId.ASSEMBLED_PLAN],
            required_acceptance_checks=["facts_schema"],
        )
    return None


__all__ = ["assembled_plan_issue_owner"]
