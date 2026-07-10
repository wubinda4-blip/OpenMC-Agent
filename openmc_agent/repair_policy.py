from __future__ import annotations

from typing import Sequence

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

# Keep error-code constants here so tests and callers do not need to depend on
# the larger validation catalog for repair-proposal-local failures.
REPAIR_INVALID_JSON = "repair.invalid_json"
REPAIR_SCHEMA_INVALID = "repair.schema_invalid"
REPAIR_MISSING_SOURCE_ISSUE = "repair.missing_source_issue"
REPAIR_UNKNOWN_ISSUE_CODE = "repair.unknown_issue_code"
REPAIR_OPERATION_NOT_ALLOWED = "repair.operation_not_allowed"
REPAIR_PATH_NOT_ALLOWED_FOR_ISSUE = "repair.path_not_allowed_for_issue"
REPAIR_PROTECTED_PATH = "repair.protected_path"
REPAIR_ROOT_REPLACEMENT_FORBIDDEN = "repair.root_replacement_forbidden"
REPAIR_VALUE_TYPE_MISMATCH = "repair.value_type_mismatch"
REPAIR_PATCH_APPLICATION_FAILED = "repair.patch_application_failed"
REPAIR_PLAN_SCHEMA_INVALID_AFTER = "repair.plan_schema_invalid_after"
REPAIR_TARGET_ISSUE_NOT_IMPROVED = "repair.target_issue_not_improved"
REPAIR_NEW_BLOCKING_ISSUE = "repair.new_blocking_issue"
REPAIR_REGRESSION_DETECTED = "repair.regression_detected"
REPAIR_REQUIRES_HUMAN_CONFIRMATION = "repair.requires_human_confirmation"
REPAIR_DETERMINISTIC_REPAIR_AVAILABLE = "repair.deterministic_repair_available"
REPAIR_LLM_FALLBACK_USED = "repair.llm_fallback_used"

PROTECTED_SUBSTRINGS = (
    "password",
    "secret",
    "token",
    "api_key",
    "cross_sections",
    "nuclear_data_path",
)

PROTECTED_PATH_PATTERNS = [
    "/materials/*/density*",
    "/materials/*/nuclides*",
    "/materials/*/elements*",
    "/materials/*/enrichment*",
    "/materials/*/boron*",
    "/materials/*/temperature*",
    "/complex_model/materials/*/density*",
    "/complex_model/materials/*/density_unit",
    "/complex_model/materials/*/density_value",
    "/complex_model/materials/*/composition*",
    "/complex_model/materials/*/macroscopic*",
    "/complex_model/materials/*/enrichment*",
    "/complex_model/materials/*/temperature*",
    "/benchmark*",
    "/metadata/benchmark_constants*",
    "/metadata/reference_gold*",
    "/settings/cross_sections*",
    "/settings/nuclear_data*",
    "/settings/environment*",
    "/settings/executable*",
    "/complex_model/settings/cross_sections*",
    "/complex_model/settings/nuclear_data*",
    "/complex_model/settings/environment*",
    "/complex_model/settings/executable*",
    "/complex_model/mg_cross_sections_file",
    "/core/loading_map*",
    "/core/pin_map/full_map*",
    "/complex_model/core/loading_map*",
    "/complex_model/core/pin_map/full_map*",
    "/secrets*",
    "/api_keys*",
]


def _risk_enum():
    from openmc_agent.repair_proposal import RepairRiskLevel

    return RepairRiskLevel


class RepairPathRule(AgentBaseModel):
    issue_code: str
    allowed_operations: set[str]
    allowed_path_patterns: list[str]
    denied_path_patterns: list[str] = Field(default_factory=list)
    risk_level: object = Field(default_factory=lambda: _risk_enum().LOW)
    requires_human_confirmation: bool = False
    notes: list[str] = Field(default_factory=list)


# Both root-level compact summaries and real SimulationPlan JSON are supported.
REPAIR_PATH_RULES: dict[str, RepairPathRule] = {
    "assembly3d.spacer_grid_material_slab": RepairPathRule(
        issue_code="assembly3d.spacer_grid_material_slab",
        allowed_operations={"add", "remove", "replace", "test"},
        allowed_path_patterns=[
            "/core/axial_overlays/*",
            "/core/axial_overlays/**",
            "/core/axial_layers/*/fill",
            "/core/axial_layers/*/material_id",
            "/complex_model/core/axial_overlays/*",
            "/complex_model/core/axial_overlays/**",
            "/complex_model/core/axial_layers/*/fill",
            "/complex_model/core/axial_layers/*/material_id",
        ],
        notes=["May only move grid representation into overlay metadata/skeleton fields."],
    ),
    "assembly3d.axial_overlay_target_missing": RepairPathRule(
        issue_code="assembly3d.axial_overlay_target_missing",
        allowed_operations={"add", "replace", "test"},
        allowed_path_patterns=[
            "/core/axial_overlays/*/target_universe_id",
            "/core/axial_overlays/*/target",
            "/complex_model/core/axial_overlays/*/target_universe_id",
            "/complex_model/core/axial_overlays/*/target",
        ],
    ),
    "assembly3d.axial_overlay_invalid_range": RepairPathRule(
        issue_code="assembly3d.axial_overlay_invalid_range",
        allowed_operations={"replace", "test"},
        allowed_path_patterns=[
            "/core/axial_overlays/*/z_min_cm",
            "/core/axial_overlays/*/z_max_cm",
            "/complex_model/core/axial_overlays/*/z_min_cm",
            "/complex_model/core/axial_overlays/*/z_max_cm",
        ],
    ),
    "assembly.missing_patch": RepairPathRule(
        issue_code="assembly.missing_patch",
        allowed_operations={"add", "replace", "test"},
        allowed_path_patterns=["/metadata/repair_requests/*", "/metadata/repair_requests/**"],
        risk_level=_risk_enum().MEDIUM,
        notes=["Do not synthesize full pin maps; request patch regeneration only."],
    ),
    "audit.capability.renderer_claim_conflict": RepairPathRule(
        issue_code="audit.capability.renderer_claim_conflict",
        allowed_operations={"add", "replace", "test"},
        allowed_path_patterns=[
            "/capability/renderability",
            "/capability/supported_renderer",
            "/metadata/capability_notes",
            "/metadata/capability_notes/*",
            "/capability_report/renderability",
            "/capability_report/supported_renderer",
        ],
    ),
    "audit.capability.renderability_conflict": RepairPathRule(
        issue_code="audit.capability.renderability_conflict",
        allowed_operations={"add", "replace", "test"},
        allowed_path_patterns=[
            "/capability/renderability",
            "/capability/supported_renderer",
            "/metadata/capability_notes",
            "/metadata/capability_notes/*",
            "/capability_report/renderability",
            "/capability_report/supported_renderer",
        ],
    ),
    "audit.material.nominal_reported_as_confirmed": RepairPathRule(
        issue_code="audit.material.nominal_reported_as_confirmed",
        allowed_operations={"add", "replace", "test"},
        allowed_path_patterns=[
            "/materials/*/composition_status",
            "/materials/*/metadata/composition_source",
            "/materials/*/metadata/approximation_level",
            "/complex_model/materials/*/composition_status",
            "/complex_model/materials/*/metadata/composition_source",
            "/complex_model/materials/*/metadata/approximation_level",
            "/complex_model/materials/*/source",
            "/complex_model/materials/*/source_note",
        ],
    ),
    "audit.reference.reference_policy_conflict": RepairPathRule(
        issue_code="audit.reference.reference_policy_conflict",
        allowed_operations={"add", "replace", "test"},
        allowed_path_patterns=[
            "/metadata/reference_policy",
            "/metadata/reference_usage",
            "/metadata/reference_usage/**",
        ],
    ),
    "audit.reference.unexpected_reference_usage": RepairPathRule(
        issue_code="audit.reference.unexpected_reference_usage",
        allowed_operations={"add", "replace", "test"},
        allowed_path_patterns=[
            "/metadata/reference_policy",
            "/metadata/reference_usage",
            "/metadata/reference_usage/**",
        ],
    ),
    "audit.fact_gap.unresolved_fact_hidden": RepairPathRule(
        issue_code="audit.fact_gap.unresolved_fact_hidden",
        allowed_operations={"add", "test"},
        allowed_path_patterns=["/metadata/repair_requests/*", "/metadata/repair_requests/**"],
        risk_level=_risk_enum().MEDIUM,
        requires_human_confirmation=True,
    ),
}


def decode_json_pointer(path: str) -> list[str]:
    if path == "":
        return []
    if not path.startswith("/"):
        raise ValueError("JSON pointer must start with '/'")
    parts = path.split("/")[1:]
    decoded: list[str] = []
    for part in parts:
        value = part.replace("~1", "/").replace("~0", "~")
        if ".." in value:
            raise ValueError("JSON pointer must not contain '..'")
        decoded.append(value)
    return decoded


def _safe_decode(path: str) -> list[str] | None:
    try:
        return decode_json_pointer(path)
    except ValueError:
        return None


def match_json_pointer_pattern(path: str, pattern: str) -> bool:
    path_parts = _safe_decode(path)
    pattern_parts = _safe_decode(pattern)
    if path_parts is None or pattern_parts is None:
        return False

    def match_at(i: int, j: int) -> bool:
        if j == len(pattern_parts):
            return i == len(path_parts)
        token = pattern_parts[j]
        if token == "**":
            return any(match_at(k, j + 1) for k in range(i, len(path_parts) + 1))
        if i >= len(path_parts):
            return False
        if token == "*":
            return match_at(i + 1, j + 1)
        if token.endswith("*") and token[:-1]:
            return path_parts[i].startswith(token[:-1]) and match_at(i + 1, j + 1)
        return token == path_parts[i] and match_at(i + 1, j + 1)

    return match_at(0, 0)


def is_root_replacement(path: str) -> bool:
    return path == ""


def is_protected_path(path: str) -> bool:
    if is_root_replacement(path):
        return True
    lowered = path.lower()
    if any(term in lowered for term in PROTECTED_SUBSTRINGS):
        return True
    if _safe_decode(path) is None:
        return True
    return any(match_json_pointer_pattern(path, pattern) for pattern in PROTECTED_PATH_PATTERNS)


def rules_for_issue_codes(issue_codes: Sequence[str]) -> list[RepairPathRule]:
    return [REPAIR_PATH_RULES[code] for code in issue_codes if code in REPAIR_PATH_RULES]
