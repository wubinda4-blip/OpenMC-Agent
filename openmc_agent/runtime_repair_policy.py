"""Runtime repair ownership policy: maps runtime issue codes to real patch types.

This module defines which runtime failures can be deterministically repaired,
which patch types own them, and which paths within those patches are safe to
modify. It is reactor-agnostic and does not hardcode any benchmark constants.

The policy is consulted BEFORE any repair attempt to determine whether a
deterministic repair is even possible, and if so, what constraints apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from openmc_agent.runtime_feedback import RuntimeFailureClass


# Real patch types that the incremental executor can produce.
PatchType = Literal[
    "facts", "materials", "universes", "pin_map",
    "axial_layers", "axial_overlays", "settings",
]

# Path patterns globally forbidden in any repair.
_GLOBAL_FORBIDDEN = [
    "/benchmark*",
    "/cross_sections*",
    "/nuclear_data*",
    "/environment*",
    "/api_key*",
    "/secret*",
    "/token*",
]


@dataclass(frozen=True)
class RuntimeRepairPolicy:
    """Ownership and safety policy for one runtime issue code."""

    issue_code: str
    classification: RuntimeFailureClass
    candidate_patch_types: list[str]
    preferred_patch_type: str | None = None
    allowed_path_patterns: list[str] = field(default_factory=list)
    forbidden_path_patterns: list[str] = field(default_factory=list)
    deterministic_repair_supported: bool = False
    requires_unique_diagnosis: bool = True
    requires_clone_openmc_check: bool = True
    retryable: bool = False
    requires_human_confirmation: bool = False
    description: str = ""
    # R4 LLM fields.
    llm_diagnosis_supported: bool = False
    llm_proposal_supported: bool = False
    allowed_repair_kinds: list[str] = field(default_factory=list)
    max_mutating_operations: int = 4
    minimum_diagnosis_confidence: float = 0.5
    minimum_proposal_confidence: float = 0.5
    auto_apply_risk_ceiling: str = "low"
    requires_rendered_object_mapping: bool = False


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

_RUNTIME_REPAIR_POLICIES: dict[str, RuntimeRepairPolicy] = {}


def _register(policy: RuntimeRepairPolicy) -> RuntimeRepairPolicy:
    _RUNTIME_REPAIR_POLICIES[policy.issue_code] = policy
    return policy


# --- Source rejection family: deterministic settings repair ---

_SOURCE_ALLOWED = ["/source_strategy", "/source_requires_fissionable_constraint"]
_SOURCE_FORBIDDEN = [
    "/active_fuel_region*",
    "/axial_domain*",
    "/plot_strategy",
    "/cross_sections*",
    "/tallies*",
    "/assumptions*",
    *_GLOBAL_FORBIDDEN,
]

_register(RuntimeRepairPolicy(
    issue_code="runtime.openmc_source_rejection_failure",
    classification=RuntimeFailureClass.PLAN_FIXABLE,
    candidate_patch_types=["settings"],
    preferred_patch_type="settings",
    allowed_path_patterns=_SOURCE_ALLOWED,
    forbidden_path_patterns=_SOURCE_FORBIDDEN,
    deterministic_repair_supported=True,
    requires_unique_diagnosis=False,
    requires_clone_openmc_check=True,
    retryable=True,
    llm_diagnosis_supported=True,
    llm_proposal_supported=False,
    description="Source box likely does not overlap fissionable fuel; "
                "deterministically bind source_strategy to active_fuel_box. "
                "LLM diagnosis only when deterministic repair is no-op.",
))

for _code in (
    "runtime.source_default_z_extent",
    "runtime.source_not_in_active_fuel_region",
    "runtime.source_covers_nonfuel_axial_regions",
    "runtime.source_strategy_not_rendered",
    "runtime.source_bounds_render_mismatch",
    "runtime.manual_source_bounds_missing",
    "runtime.unknown_source_strategy",
):
    _register(RuntimeRepairPolicy(
        issue_code=_code,
        classification=RuntimeFailureClass.PLAN_FIXABLE,
        candidate_patch_types=["settings"],
        preferred_patch_type="settings",
        allowed_path_patterns=_SOURCE_ALLOWED,
        forbidden_path_patterns=_SOURCE_FORBIDDEN,
        deterministic_repair_supported=True,
        requires_unique_diagnosis=False,
        requires_clone_openmc_check=True,
        retryable=True,
        description="Source extent preflight failure; deterministically "
                    "switch source_strategy to active_fuel_box.",
    ))

# --- Fuel fissionable / active fuel geometry: diagnose only ---

for _code in (
    "runtime.fuel_material_not_fissionable",
    "runtime.active_fuel_region_missing",
    "runtime.active_fuel_geometry_missing",
    "runtime.source_missing_fissionable_constraint",
):
    _register(RuntimeRepairPolicy(
        issue_code=_code,
        classification=RuntimeFailureClass.PLAN_FIXABLE,
        candidate_patch_types=["materials", "universes", "axial_layers"],
        deterministic_repair_supported=False,
        requires_unique_diagnosis=True,
        requires_human_confirmation=True,
        description="Fuel/fissionability or active-fuel geometry issue; "
                    "requires human confirmation, no auto-repair.",
    ))

# --- Geometry overlap / lost particle: diagnose only, no auto-repair in R3 ---

_GEOMETRY_FORBIDDEN = [
    "/composition*",
    "/density*",
    "/enrichment*",
    "/temperature*",
    "/mixture*",
    "/total_mass*",
    "/frame*",
    "/pin_map*",
    "/coordinate*",
    "/loading*",
    *_GLOBAL_FORBIDDEN,
]

for _code in (
    "runtime.geometry_overlap",
    "runtime.lost_particle",
):
    _register(RuntimeRepairPolicy(
        issue_code=_code,
        classification=RuntimeFailureClass.PLAN_FIXABLE,
        candidate_patch_types=["universes", "axial_layers", "axial_overlays", "pin_map"],
        allowed_path_patterns=[],  # Must be narrowed by diagnosis
        forbidden_path_patterns=_GEOMETRY_FORBIDDEN,
        deterministic_repair_supported=False,
        requires_unique_diagnosis=True,
        requires_clone_openmc_check=True,
        llm_diagnosis_supported=True,
        llm_proposal_supported=True,
        allowed_repair_kinds=[
            "reference_correction",
            "duplicate_reference_removal",
            "restore_existing_topology_constraint",
            "align_redundant_boundary_to_existing_value",
        ],
        max_mutating_operations=4,
        minimum_diagnosis_confidence=0.6,
        minimum_proposal_confidence=0.6,
        requires_rendered_object_mapping=True,
        description="Geometry overlap/lost-particle; LLM diagnosis + constrained "
                    "proposal for reference correction and topology restoration only.",
    ))

# --- Material missing nuclide: human fact ---

_register(RuntimeRepairPolicy(
    issue_code="runtime.material_missing_nuclide_data",
    classification=RuntimeFailureClass.HUMAN_FACT,
    candidate_patch_types=["materials"],
    allowed_path_patterns=["/composition/*/name"],
    forbidden_path_patterns=[
        "/density*", "/enrichment*", "/temperature*",
        "/mixture*", *_GLOBAL_FORBIDDEN,
    ],
    deterministic_repair_supported=False,
    requires_human_confirmation=True,
    description="Nuclide data missing from library; requires human confirmation. "
                "Name normalization (GND hyphen removal) is handled separately.",
))

# --- Environment: blocked ---

for _code, _desc in (
    ("runtime.cross_sections_missing", "Cross-section data missing or not configured."),
    ("runtime.cross_sections_invalid", "Cross-section data path or XML invalid."),
):
    _register(RuntimeRepairPolicy(
        issue_code=_code,
        classification=RuntimeFailureClass.ENVIRONMENT,
        candidate_patch_types=[],
        deterministic_repair_supported=False,
        requires_human_confirmation=True,
        description=_desc,
    ))

# --- Transient: no plan repair ---

for _code, _desc in (
    ("runtime.openmc_timeout", "OpenMC process exceeded timeout."),
    ("runtime.openmc_process_crash", "OpenMC process crashed."),
):
    _register(RuntimeRepairPolicy(
        issue_code=_code,
        classification=RuntimeFailureClass.TRANSIENT,
        candidate_patch_types=[],
        deterministic_repair_supported=False,
        description=_desc,
    ))

# --- Unknown: no plan repair ---

_register(RuntimeRepairPolicy(
    issue_code="runtime.openmc_unknown_error",
    classification=RuntimeFailureClass.UNKNOWN,
    candidate_patch_types=[],
    deterministic_repair_supported=False,
    requires_human_confirmation=True,
    description="Unknown runtime error; requires manual investigation.",
))

# --- Export XML dangling refs: reflect_plan candidate, not runtime repair ---

for _code in (
    "export_xml.dangling_cell_fill",
    "export_xml.dangling_lattice_universe",
    "export_xml.dangling_lattice_outer_universe",
):
    _register(RuntimeRepairPolicy(
        issue_code=_code,
        classification=RuntimeFailureClass.PLAN_FIXABLE,
        candidate_patch_types=["universes", "pin_map", "axial_layers"],
        deterministic_repair_supported=False,
        requires_unique_diagnosis=True,
        llm_diagnosis_supported=True,
        llm_proposal_supported=True,
        allowed_repair_kinds=[
            "reference_correction",
            "duplicate_reference_removal",
        ],
        max_mutating_operations=2,
        minimum_diagnosis_confidence=0.7,
        minimum_proposal_confidence=0.7,
        description="Dangling XML reference; LLM can diagnose and propose "
                    "reference correction to existing unique candidate.",
    ))


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

RUNTIME_REPAIR_POLICIES: dict[str, RuntimeRepairPolicy] = dict(_RUNTIME_REPAIR_POLICIES)


def get_repair_policy(issue_code: str) -> RuntimeRepairPolicy | None:
    """Return the repair policy for *issue_code*, or ``None`` if unregistered."""
    return _RUNTIME_REPAIR_POLICIES.get(issue_code)


def is_environment_blocked(issue_codes: list[str]) -> bool:
    """True if any issue code is an environment-only blocker."""
    return any(
        _RUNTIME_REPAIR_POLICIES.get(code) is not None
        and _RUNTIME_REPAIR_POLICIES[code].classification
        is RuntimeFailureClass.ENVIRONMENT
        for code in issue_codes
    )
