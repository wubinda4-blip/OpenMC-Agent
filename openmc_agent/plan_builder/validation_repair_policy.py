"""Ownership and safety policy for incremental validation repair."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel


class ValidationIssueRepairPolicy(AgentBaseModel):
    issue_code: str
    owner_patch_type: str
    allowed_path_patterns: list[str]
    forbidden_path_patterns: list[str] = Field(default_factory=list)
    retryable: bool = True
    requires_human_confirmation: bool = False
    preferred_strategy: Literal[
        "deterministic", "patch_edit", "full_patch_regeneration", "dependency_repair"
    ] = "patch_edit"


# Patch-relative JSON Pointer paths.  Global protected paths are enforced in
# addition to this registry, so no policy can authorize materials facts or
# nuclear-data/environment configuration.
VALIDATION_ISSUE_REPAIR_POLICIES: dict[str, ValidationIssueRepairPolicy] = {
    "assembly3d.component_profile_as_material_slab": ValidationIssueRepairPolicy(
        issue_code="assembly3d.component_profile_as_material_slab",
        owner_patch_type="axial_layers",
        allowed_path_patterns=[
            "/layers/*/fill_type",
            "/layers/*/fill_id",
            "/layers/*/loading_id",
            "/layers/*/loading_ids",
            "/layers/*/loading_ids/**",
            "/lattice_loadings",
            "/lattice_loadings/**",
        ],
        forbidden_path_patterns=["/layers/*/role"],
        preferred_strategy="deterministic",
    ),
    "lattice.universe_missing_coolant": ValidationIssueRepairPolicy(
        issue_code="lattice.universe_missing_coolant",
        owner_patch_type="universes",
        allowed_path_patterns=["/universes/*/cells", "/universes/*/cells/**"],
    ),
    "lattice_loading.override_universe_ref_missing": ValidationIssueRepairPolicy(
        issue_code="lattice_loading.override_universe_ref_missing",
        owner_patch_type="axial_layers",
        allowed_path_patterns=["/lattice_loadings/*/overrides/**", "/lattice_loadings/*/transformations/**"],
        preferred_strategy="dependency_repair",
    ),
    "lattice.pin_count_mismatch": ValidationIssueRepairPolicy(
        issue_code="lattice.pin_count_mismatch",
        owner_patch_type="pin_map",
        allowed_path_patterns=[
            "/default_universe_id", "/guide_tube_coords", "/guide_tube_coords/**",
            "/instrument_tube_coords", "/instrument_tube_coords/**", "/water_cell_coords",
            "/water_cell_coords/**",
        ],
    ),
    "axial_layer.loading_ref_missing": ValidationIssueRepairPolicy(
        issue_code="axial_layer.loading_ref_missing",
        owner_patch_type="axial_layers",
        allowed_path_patterns=["/layers/*/loading_id", "/layers/*/loading_ids", "/layers/*/loading_ids/**", "/lattice_loadings/**"],
    ),
    "axial_layer.fill_ref_missing": ValidationIssueRepairPolicy(
        issue_code="axial_layer.fill_ref_missing",
        owner_patch_type="axial_layers",
        allowed_path_patterns=["/layers/*/fill_id", "/layers/*/fill_type"],
    ),
}


def policy_for_issue_code(issue_code: str) -> ValidationIssueRepairPolicy | None:
    return VALIDATION_ISSUE_REPAIR_POLICIES.get(issue_code)
