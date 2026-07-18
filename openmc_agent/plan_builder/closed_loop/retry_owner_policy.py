"""Deterministic owner/action registry for executable retry requests."""

from __future__ import annotations

from typing import Any

from openmc_agent.schemas import AgentBaseModel

from .models import PlanGateId
from .retry_models import PlanRetryAction


class RetryOwnerPolicy(AgentBaseModel):
    owner_patch_types: list[str]
    preferred_action: PlanRetryAction
    fallback_action: PlanRetryAction = PlanRetryAction.FAIL_CLOSED
    invalidated_dependents: bool = True
    gates_to_invalidate: list[PlanGateId] = []
    required_acceptance_checks: list[str] = []
    requires_human_when_ambiguous: bool = True
    max_attempts: int = 2
    supported_modes: list[str] = ["controlled", "advisory"]
    protected_json_paths: list[str] = ["/patch_type"]


_MATERIAL_UNIVERSE_FACTS_CODES = {
    "material_universe.required_fuel_variant_missing",
}

_FACTS_CODES = {
    "facts.model_scope_conflicts_with_planning_features",
    "facts.multi_assembly_contract_incomplete",
    "facts.localized_insert_contract_missing",
    "facts.localized_insert_profile_contract_missing",
    "facts.control_state_contract_missing",
    "facts.fuel_variant_contract_missing",
    "facts.assembly_count_inconsistent",
    "facts.core_lattice_size_inconsistent",
    "assembly.model_scope_patch_family_conflict",
} | _MATERIAL_UNIVERSE_FACTS_CODES
_MATERIAL_CODES = {
    "materials.execution_density_required",
    "assembly.unresolved_material_reference",
    "materials.compound_in_transport_composition",
    "materials.unsupported_compound_formula",
    "materials.unresolved_species",
    # Phase-4 Material-Universe Gate deterministic codes routed to materials owner.
    "material_universe.material_duplicate",
    "material_universe.material_density_invalid",
    "material_universe.transport_species_invalid",
    "material_universe.compound_in_transport_composition",
    "material_universe.compound_fraction_basis_missing",
    "material_universe.fissile_isotope_policy_missing",
    "material_universe.alloy_reduced_without_disclosure",
    "material_universe.required_material_missing",
    "material_universe.required_fuel_variant_material_missing",
    "material_universe.fuel_variant_material_duplicate",
    "material_universe.placeholder_material_unresolved",
    "material_universe.enrichment_contract_mismatch",
}
_UNIVERSE_CODES = {
    "localized_insert.required_universe_missing",
    "patch.pin_map.default_universe_missing",
    "assembly_catalog.universe_missing",
    "assembly.unresolved_universe_reference",
    "profile.segment_universe_missing",
    "required_fuel_universe_missing",
    # Phase-4 Material-Universe Gate deterministic codes routed to universes owner.
    "material_universe.universe_duplicate",
    "material_universe.universe_empty",
    "material_universe.cell_duplicate",
    "material_universe.invalid_radial_order",
    "material_universe.radial_gap",
    "material_universe.radial_overlap",
    "material_universe.background_missing",
    "material_universe.material_reference_missing",
    "material_universe.material_role_mismatch",
    "material_universe.multiple_variants_in_one_universe",
    "material_universe.fuel_variant_collapsed",
    "material_universe.fuel_variant_material_unreachable",
    "material_universe.variant_identity_mismatch",
}
_TASK_PLAN_CODES = {
    "planning.required_patch_omitted",
    "planning.mixed_patch_family",
    "planning.stale_canonical_task_plan",
    "planning.task_plan_hash_mismatch",
}
_PLACEMENT_CODES = {
    "localized_insert.required_placement_missing",
    "localized_insert.required_assembly_type_missing",
    "localized_insert.required_profile_missing",
    "localized_insert.required_profile_unused",
    "localized_insert.coordinate_count_mismatch",
    "localized_insert.coordinates_not_in_host_path",
    "localized_insert.coordinate_duplicate",
    "localized_insert.instrument_path_misused",
    "localized_insert.anchor_mismatch",
    "localized_insert.control_state_mismatch",
    "localized_insert.core_multiplicity_mismatch",
    "localized_insert.unexpected_assembly_scope",
}

# Phase-4 Material-Universe Gate issue codes.  These overlap with Materials/
# Universes owner codes so the Phase-3B retry loop can drive them through the
# same producer registry.  Fuel-variant root causes that trace back to an
# incomplete source contract route to Facts as a dependency retry.


def _resolve_placement_owner(issue: dict[str, Any], scope: str | None, code: str = "") -> list[str]:
    """Pick exactly one placement owner for a given canonical scope.

    The two assembly-family representations (top-level ``pin_map`` vs
    ``assembly_catalog`` + ``core_layout``) are mutually exclusive.  Returning
    both at once would ask the producer to regenerate two patches that can
    never coexist, so we fail closed when the scope is ambiguous.
    """
    declared = str(issue.get("owner_patch_type") or "")
    # Core-layout and profile owners are scope-independent.
    if declared in {"localized_insert_profiles", "core_layout"}:
        return [declared]
    # First, try code-specific scope-aware resolution from the placement
    # issue policy.  This correctly handles codes like
    # ``core_multiplicity_mismatch`` whose owner is always ``core_layout``
    # in multi-assembly scope, not ``assembly_catalog``.
    effective_code = code or str(issue.get("code") or "")
    if effective_code:
        from .placement_issue_policy import placement_issue_owner
        owner_dict = placement_issue_owner(effective_code, canonical_scope=scope)
        owners = owner_dict.get("owner_patch_types", [])
        # Filter out the mutually-exclusive pair if both appear (unknown
        # scope fallback returned both).
        if owners and not ({"pin_map", "assembly_catalog"}.issubset(set(owners))):
            return owners
    # Fall back to declared owner or scope-based default.
    if declared in {"pin_map", "assembly_catalog"}:
        if scope in {"single_assembly"} and declared == "pin_map":
            return ["pin_map"]
        if scope in {"multi_assembly", "full_core"} and declared == "assembly_catalog":
            return ["assembly_catalog"]
        return [declared]
    if scope in {"single_assembly"}:
        return ["pin_map"]
    if scope in {"multi_assembly", "full_core"}:
        return ["assembly_catalog"]
    return []


def retry_owner_policy(code: str, issue: dict[str, Any] | None = None, *, canonical_scope: str | None = None) -> RetryOwnerPolicy | None:
    if code in _FACTS_CODES:
        return RetryOwnerPolicy(
            owner_patch_types=["facts"], preferred_action=PlanRetryAction.REVISE_OWNER_PATCH,
            fallback_action=PlanRetryAction.REGENERATE_OWNER_PATCH,
            gates_to_invalidate=[PlanGateId.FACTS, PlanGateId.PLACEMENT],
            required_acceptance_checks=["facts_schema", "facts_consistency", "resolved_scope", "source_critical_feature_coverage", "facts_critic", "canonical_task_plan"],
        )
    if code in _MATERIAL_CODES:
        return RetryOwnerPolicy(
            owner_patch_types=["materials"], preferred_action=PlanRetryAction.REVISE_OWNER_PATCH,
            fallback_action=PlanRetryAction.REGENERATE_OWNER_PATCH,
            gates_to_invalidate=[PlanGateId.MATERIAL_UNIVERSE, PlanGateId.PLACEMENT],
            required_acceptance_checks=["materials_schema", "material_species", "composition_basis", "fuel_variant_identity", "density_policy", "material_readiness"],
            protected_json_paths=["/patch_type", "/materials/*/material_id", "/materials/*/role", "/materials/*/fuel_enrichment"],
        )
    if code in _UNIVERSE_CODES:
        return RetryOwnerPolicy(
            owner_patch_types=["universes"], preferred_action=PlanRetryAction.REGENERATE_OWNER_PATCH,
            gates_to_invalidate=[PlanGateId.PLACEMENT],
            required_acceptance_checks=["universes_schema", "material_references", "required_universe_ids", "cell_geometry_local", "through_path", "fuel_variant_reachability", "profile_references", "placement_preflight"],
            protected_json_paths=["/patch_type", "/universes/*/fuel_variant_id"],
        )
    if code in _TASK_PLAN_CODES:
        return RetryOwnerPolicy(
            owner_patch_types=["planning_task_plan"], preferred_action=PlanRetryAction.RECOMPUTE_TASK_PLAN,
            gates_to_invalidate=[PlanGateId.PLACEMENT],
            required_acceptance_checks=["canonical_task_plan", "patch_family"],
        )
    if code in _PLACEMENT_CODES:
        owner_types = _resolve_placement_owner(issue or {}, canonical_scope, code=code)
        if not owner_types:
            return None
        return RetryOwnerPolicy(
            owner_patch_types=owner_types, preferred_action=PlanRetryAction.REVISE_OWNER_PATCH,
            fallback_action=PlanRetryAction.REGENERATE_OWNER_PATCH,
            gates_to_invalidate=[PlanGateId.PLACEMENT],
            required_acceptance_checks=["placement_preflight", "placement_critic", "placement_contract_coverage"],
            protected_json_paths=["/patch_type", "/facts", "/materials", "/universes"],
        )
    if code.startswith("patch.axial_") or code.startswith("patch.base_path_axial_profiles"):
        owner = str((issue or {}).get("patch_type") or "axial_overlays")
        if owner in {"axial_layers", "axial_overlays", "base_path_axial_profiles"}:
            return RetryOwnerPolicy(
                owner_patch_types=[owner], preferred_action=PlanRetryAction.REGENERATE_OWNER_PATCH,
                gates_to_invalidate=[PlanGateId.AXIAL_GEOMETRY],
                required_acceptance_checks=["patch_schema", "patch_validation"],
            )
    # Phase-5 Axial Geometry Gate issue codes.
    if code.startswith("axial."):
        from .axial_geometry_issue_policy import axial_geometry_issue_owner
        return axial_geometry_issue_owner(code, issue)
    # Phase-6 Assembled Plan Gate issue codes.
    if code.startswith("assembled."):
        from .assembled_plan_issue_policy import assembled_plan_issue_owner
        return assembled_plan_issue_owner(code, issue)
    return None


def registered_retry_issue_codes() -> set[str]:
    return _FACTS_CODES | _MATERIAL_CODES | _UNIVERSE_CODES | _TASK_PLAN_CODES | _PLACEMENT_CODES
