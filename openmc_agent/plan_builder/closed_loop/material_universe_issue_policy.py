"""Deterministic owner/action policy for Material-Universe Gate issues.

The Critic never decides the owner.  Python maps each issue code to a
concrete owner patch type and retry action, following the upstream-priority
rule: Facts → Materials → Universes.
"""

from __future__ import annotations

from typing import Any

from .models import PlanReviewAction


# Materials-owned: the material entry itself is wrong.
_MATERIALS_CODES = {
    "material_universe.material_duplicate",
    "material_universe.material_density_invalid",
    "material_universe.material_density_missing",
    "material_universe.transport_species_invalid",
    "material_universe.compound_in_transport_composition",
    "material_universe.compound_fraction_basis_missing",
    "material_universe.compound_isotope_unresolved",
    "material_universe.compound_isotope_policy_missing",
    "material_universe.fissile_isotope_policy_missing",
    "material_universe.alloy_reduced_without_disclosure",
    "material_universe.required_material_missing",
    "material_universe.material_requirement_missing",
    "material_universe.required_density_missing",
    "material_universe.required_fuel_variant_material_missing",
    "material_universe.fuel_variant_material_duplicate",
    "material_universe.material_source_variant_unknown",
    "material_universe.placeholder_material_unresolved",
    "material_universe.material_provenance_missing",
    "material_universe.density_provenance_missing",
    "material_universe.materials_schema_invalid",
    "material_universe.invalid_composition_sum_for_basis",
    "material_universe.contract_material_id_mismatch",
    "material_universe.contract_material_role_mismatch",
}

# Universes-owned: the universe/cell structure or reference is wrong.
_UNIVERSES_CODES = {
    "material_universe.universe_duplicate",
    "material_universe.universe_empty",
    "material_universe.required_universe_missing",
    "material_universe.cell_duplicate",
    "material_universe.invalid_radial_order",
    "material_universe.radial_gap",
    "material_universe.radial_overlap",
    "material_universe.background_missing",
    "material_universe.material_reference_missing",
    "material_universe.material_role_mismatch",
    "material_universe.fuel_cell_missing",
    "material_universe.guide_tube_wall_missing",
    "material_universe.guide_tube_moderator_missing",
    "material_universe.insert_material_missing",
    "material_universe.localized_insert_universe_missing",
    "material_universe.protected_path_missing",
    "material_universe.profile_material_structure_incomplete",
    "material_universe.multiple_variants_in_one_universe",
    "material_universe.fuel_variant_material_unreachable",
    "material_universe.fuel_variant_material_mismatch",
    "material_universe.fuel_variant_collapsed",
    "material_universe.variant_identity_mismatch",
    "material_universe.universes_schema_invalid",
    "material_universe.material_role_conflict",
    "material_universe.material_count_role_count_mismatch",
}

# Fuel-variant issues where the root cause is the material (not the universe).
_MATERIALS_VARIANT_CODES = {
    "material_universe.required_fuel_variant_material_missing",
    "material_universe.fuel_variant_material_duplicate",
    "material_universe.enrichment_contract_mismatch",
}

# Universes-variant issues where the root cause is the universe structure.
_UNIVERSES_VARIANT_CODES = {
    "material_universe.multiple_variants_in_one_universe",
    "material_universe.fuel_variant_collapsed",
    "material_universe.fuel_variant_material_mismatch",
    "material_universe.fuel_variant_material_unreachable",
    "material_universe.variant_identity_mismatch",
}

# Facts dependency: source contract itself is incomplete.
_FACTS_DEPENDENCY_CODES = {
    "material_universe.required_fuel_variant_missing",
    # When the source itself never specified the fuel variant / enrichment.
}


def material_universe_issue_owner(code: str, issue: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the deterministic owner for a Material-Universe issue code.

    A single issue never maps to both materials and universes; when both
    could be involved, upstream priority picks materials first.  Returns
    ``{}`` (fail closed) for unknown codes.
    """
    issue = issue or {}
    if code in _FACTS_DEPENDENCY_CODES:
        return {"dependency_patch_type": "facts", "allowed_actions": [PlanReviewAction.RETRY_DEPENDENCY]}
    if code in _MATERIALS_CODES or code in _MATERIALS_VARIANT_CODES:
        return {"owner_patch_types": ["materials"], "allowed_actions": [PlanReviewAction.REVISE_CURRENT_PATCH, PlanReviewAction.RETRY_DEPENDENCY]}
    if code in _UNIVERSES_CODES or code in _UNIVERSES_VARIANT_CODES:
        return {"owner_patch_types": ["universes"], "allowed_actions": [PlanReviewAction.REVISE_CURRENT_PATCH, PlanReviewAction.RETRY_DEPENDENCY]}
    return {}


def registered_material_universe_issue_codes() -> set[str]:
    return _MATERIALS_CODES | _UNIVERSES_CODES | _MATERIALS_VARIANT_CODES | _UNIVERSES_VARIANT_CODES | _FACTS_DEPENDENCY_CODES


__all__ = ["material_universe_issue_owner", "registered_material_universe_issue_codes"]
