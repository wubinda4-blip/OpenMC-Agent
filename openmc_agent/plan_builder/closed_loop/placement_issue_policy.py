"""Deterministic ownership for Placement Gate issues."""

from __future__ import annotations

from typing import Any

from .models import PlanReviewAction

_OWNER: dict[str, dict[str, Any]] = {
    "localized_insert.required_placement_missing": {"owner_patch_types": ["assembly_catalog", "pin_map"], "repairable_paths": ["/assembly_types", "/localized_insert_intents"]},
    "localized_insert.required_assembly_type_missing": {"owner_patch_types": ["assembly_catalog"], "repairable_paths": ["/assembly_types"]},
    "localized_insert.required_profile_missing": {"owner_patch_types": ["localized_insert_profiles", "assembly_catalog", "pin_map"], "repairable_paths": ["/profiles", "/localized_insert_intents"]},
    "localized_insert.required_profile_unused": {"owner_patch_types": ["assembly_catalog", "pin_map"], "repairable_paths": ["/localized_insert_intents"]},
    "localized_insert.coordinate_count_mismatch": {"owner_patch_types": ["assembly_catalog", "pin_map"], "repairable_paths": ["/localized_insert_intents"]},
    "localized_insert.coordinates_not_in_host_path": {"owner_patch_types": ["assembly_catalog", "pin_map"], "repairable_paths": ["/localized_insert_intents", "/guide_tube_coords", "/instrument_tube_coords"]},
    "localized_insert.coordinate_duplicate": {"owner_patch_types": ["assembly_catalog", "pin_map"], "repairable_paths": ["/localized_insert_intents"]},
    "localized_insert.instrument_path_misused": {"owner_patch_types": ["assembly_catalog", "pin_map"], "repairable_paths": ["/localized_insert_intents"]},
    "localized_insert.anchor_mismatch": {"owner_patch_types": ["assembly_catalog", "pin_map", "localized_insert_profiles"], "repairable_paths": ["/localized_insert_intents", "/profiles"]},
    "localized_insert.control_state_mismatch": {"owner_patch_types": ["assembly_catalog", "pin_map"], "repairable_paths": ["/localized_insert_intents"]},
    "localized_insert.core_multiplicity_mismatch": {"owner_patch_types": ["core_layout"], "repairable_paths": ["/assembly_pattern", "/expected_assembly_type_counts"]},
    "localized_insert.unexpected_assembly_scope": {"owner_patch_types": ["core_layout"], "repairable_paths": ["/assembly_pattern"]},
    "localized_insert.required_universe_missing": {"dependency_patch_type": "universes"},
    "localized_insert.no_absorber_overlap_with_domain": {"cross_gate_dependency": "axial"},
}


def placement_issue_owner(code: str) -> dict[str, Any]:
    value = dict(_OWNER.get(code, {}))
    value.setdefault("protected_paths", ["/patch_type"])
    if "dependency_patch_type" in value:
        value["allowed_actions"] = [PlanReviewAction.RETRY_DEPENDENCY]
    elif "cross_gate_dependency" in value:
        value["allowed_actions"] = [PlanReviewAction.FAIL_CLOSED]
    else:
        value["allowed_actions"] = [PlanReviewAction.REVISE_CURRENT_PATCH, PlanReviewAction.FAIL_CLOSED]
    return value


def placement_owner_patch_types(code: str) -> list[str]:
    return list(placement_issue_owner(code).get("owner_patch_types", []))
