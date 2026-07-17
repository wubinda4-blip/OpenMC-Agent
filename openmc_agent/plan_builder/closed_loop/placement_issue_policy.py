"""Deterministic ownership for Placement Gate issues."""

from __future__ import annotations

from typing import Any

from .models import PlanReviewAction

_OWNER: dict[str, dict[str, Any]] = {
    "localized_insert.required_placement_missing": {"scope_owners": {"single_assembly": ["pin_map"], "multi_assembly": ["assembly_catalog"], "full_core": ["assembly_catalog"]}, "repairable_paths": ["/assembly_types", "/localized_insert_intents"]},
    "localized_insert.required_assembly_type_missing": {"scope_owners": {"single_assembly": ["pin_map"], "multi_assembly": ["assembly_catalog"], "full_core": ["assembly_catalog"]}, "repairable_paths": ["/assembly_types"]},
    "localized_insert.required_profile_missing": {"scope_owners": {"single_assembly": ["localized_insert_profiles"], "multi_assembly": ["localized_insert_profiles"], "full_core": ["localized_insert_profiles"]}, "repairable_paths": ["/profiles", "/localized_insert_intents"]},
    "localized_insert.required_profile_unused": {"scope_owners": {"single_assembly": ["pin_map"], "multi_assembly": ["assembly_catalog"], "full_core": ["assembly_catalog"]}, "repairable_paths": ["/localized_insert_intents"]},
    "localized_insert.coordinate_count_mismatch": {"scope_owners": {"single_assembly": ["pin_map"], "multi_assembly": ["assembly_catalog"], "full_core": ["assembly_catalog"]}, "repairable_paths": ["/localized_insert_intents"]},
    "localized_insert.coordinates_not_in_host_path": {"scope_owners": {"single_assembly": ["pin_map"], "multi_assembly": ["assembly_catalog"], "full_core": ["assembly_catalog"]}, "repairable_paths": ["/localized_insert_intents", "/guide_tube_coords", "/instrument_tube_coords"]},
    "localized_insert.coordinate_duplicate": {"scope_owners": {"single_assembly": ["pin_map"], "multi_assembly": ["assembly_catalog"], "full_core": ["assembly_catalog"]}, "repairable_paths": ["/localized_insert_intents"]},
    "localized_insert.instrument_path_misused": {"scope_owners": {"single_assembly": ["pin_map"], "multi_assembly": ["assembly_catalog"], "full_core": ["assembly_catalog"]}, "repairable_paths": ["/localized_insert_intents"]},
    "localized_insert.anchor_mismatch": {"scope_owners": {"single_assembly": ["pin_map", "localized_insert_profiles"], "multi_assembly": ["assembly_catalog", "localized_insert_profiles"], "full_core": ["assembly_catalog", "localized_insert_profiles"]}, "repairable_paths": ["/localized_insert_intents", "/profiles"]},
    "localized_insert.control_state_mismatch": {"scope_owners": {"single_assembly": ["pin_map"], "multi_assembly": ["assembly_catalog"], "full_core": ["assembly_catalog"]}, "repairable_paths": ["/localized_insert_intents"]},
    "localized_insert.core_multiplicity_mismatch": {"scope_owners": {"single_assembly": ["pin_map"], "multi_assembly": ["core_layout"], "full_core": ["core_layout"]}, "repairable_paths": ["/assembly_pattern", "/expected_assembly_type_counts"]},
    "localized_insert.unexpected_assembly_scope": {"scope_owners": {"single_assembly": ["pin_map"], "multi_assembly": ["core_layout"], "full_core": ["core_layout"]}, "repairable_paths": ["/assembly_pattern"]},
    "localized_insert.required_universe_missing": {"dependency_patch_type": "universes"},
    "localized_insert.no_absorber_overlap_with_domain": {"cross_gate_dependency": "axial"},
}


def placement_issue_owner(code: str, *, canonical_scope: str | None = None) -> dict[str, Any]:
    entry = _OWNER.get(code, {})
    value: dict[str, Any] = {}
    if "scope_owners" in entry:
        if canonical_scope:
            owners = entry["scope_owners"].get(canonical_scope) or entry["scope_owners"].get("multi_assembly") or []
        else:
            # Scope unknown: the revision evaluator filters by what actually
            # exists in state, so expose owners from every scope.  This never
            # produces a simultaneous pin_map+assembly_catalog producer because
            # the typed owner policy (retry_owner_policy) is the sole authority
            # for producer selection and it always knows the canonical scope.
            owners = sorted({item for owners in entry["scope_owners"].values() for item in owners})
        value["owner_patch_types"] = list(owners)
        value["repairable_paths"] = list(entry.get("repairable_paths", []))
    elif "dependency_patch_type" in entry:
        value["dependency_patch_type"] = entry["dependency_patch_type"]
    elif "cross_gate_dependency" in entry:
        value["cross_gate_dependency"] = entry["cross_gate_dependency"]
    value.setdefault("protected_paths", ["/patch_type"])
    if "dependency_patch_type" in value:
        value["allowed_actions"] = [PlanReviewAction.RETRY_DEPENDENCY]
    elif "cross_gate_dependency" in value:
        value["allowed_actions"] = [PlanReviewAction.FAIL_CLOSED]
    else:
        value["allowed_actions"] = [PlanReviewAction.REVISE_CURRENT_PATCH, PlanReviewAction.FAIL_CLOSED]
    return value


def placement_owner_patch_types(code: str, *, canonical_scope: str | None = None) -> list[str]:
    return list(placement_issue_owner(code, canonical_scope=canonical_scope).get("owner_patch_types", []))
