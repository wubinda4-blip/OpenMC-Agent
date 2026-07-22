"""Material-Universe replay finding classification.

This module is intentionally deterministic and reviewer-independent.  It
does not decide physics; it classifies a normalized MU finding into the
owner route used by replay closure reports.  Unknown codes fail closed.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .material_universe_issue_policy import (
    material_universe_issue_owner,
    registered_material_universe_issue_codes,
)

MaterialUniverseClosureClass = Literal[
    "reviewer_false_positive",
    "deterministic_preflight_gap",
    "materials_contract_gap",
    "universes_contract_gap",
    "binding_metadata_gap",
    "true_source_gap",
]


class MaterialUniverseFindingClassification(AgentBaseModel):
    code: str
    classification: MaterialUniverseClosureClass | Literal["unknown_code"] = "unknown_code"
    owner_route: dict[str, Any] = Field(default_factory=dict)
    fail_closed: bool = False
    reason: str = ""


_DETERMINISTIC_PREFLIGHT_CODES = {
    "material_universe.background_missing",
    "material_universe.enrichment_contract_mismatch",
    "material_universe.invalid_composition_sum_for_basis",
}

_BINDING_METADATA_CODES = {
    "material_universe.contract_material_id_mismatch",
    "material_universe.material_count_role_count_mismatch",
}

_REVIEWER_FALSE_POSITIVE_CODES = {
    # Role-level source contracts do not imply distinct material IDs when
    # the source only names a shared physical material role.  Replaying this
    # as a blocker would ask production to invent specificity not present in
    # the contract.
    "material_universe.contract_material_role_mismatch",
    # UniverseRecord.material_ids is a de-duplicated set, not a cell-aligned
    # material vector.  Cell-level binding rows carry the authoritative map.
    "material_universe.material_role_conflict",
}

_TRUE_SOURCE_GAP_CODES = {
    "material_universe.required_fuel_variant_missing",
}


def classify_material_universe_finding(
    finding_or_code: Any,
) -> MaterialUniverseFindingClassification:
    """Classify one normalized MU finding or code.

    Unknown codes deliberately return ``fail_closed=True`` so replay cannot
    silently downgrade new reviewer behavior.
    """
    if isinstance(finding_or_code, str):
        code = finding_or_code
        affected_patch_types: list[str] = []
    elif isinstance(finding_or_code, dict):
        code = str(finding_or_code.get("code", ""))
        affected_patch_types = [str(item) for item in finding_or_code.get("affected_patch_types", [])]
    else:
        code = str(getattr(finding_or_code, "code", ""))
        affected_patch_types = [str(item) for item in getattr(finding_or_code, "affected_patch_types", [])]

    owner_route = material_universe_issue_owner(code)
    if not owner_route and code not in registered_material_universe_issue_codes():
        return MaterialUniverseFindingClassification(
            code=code,
            classification="unknown_code",
            owner_route={},
            fail_closed=True,
            reason="unknown Material-Universe finding code",
        )

    if code in _TRUE_SOURCE_GAP_CODES or owner_route.get("dependency_patch_type") == "facts":
        classification: MaterialUniverseClosureClass = "true_source_gap"
        reason = "finding is rooted in an upstream source/Facts dependency"
    elif code in _DETERMINISTIC_PREFLIGHT_CODES:
        classification = "deterministic_preflight_gap"
        reason = "finding is already covered by deterministic MU preflight"
    elif code in _BINDING_METADATA_CODES:
        classification = "binding_metadata_gap"
        reason = "finding points at the generated binding/matrix metadata, not reviewer policy"
    elif code in _REVIEWER_FALSE_POSITIVE_CODES:
        classification = "reviewer_false_positive"
        reason = "finding is not a valid blocker under the normalized MU scope contract"
    elif owner_route.get("owner_patch_types") == ["materials"] or affected_patch_types == ["materials"]:
        classification = "materials_contract_gap"
        reason = "owner policy routes this code to Materials"
    elif owner_route.get("owner_patch_types") == ["universes"] or affected_patch_types == ["universes"]:
        classification = "universes_contract_gap"
        reason = "owner policy routes this code to Universes"
    else:
        classification = "binding_metadata_gap"
        reason = "finding spans Materials and Universes binding metadata"

    return MaterialUniverseFindingClassification(
        code=code,
        classification=classification,
        owner_route=owner_route,
        fail_closed=False,
        reason=reason,
    )


def material_universe_finding_diagnostics(review_result: Any) -> dict[str, Any]:
    """Return sanitized replay diagnostics for normalized MU review output."""
    findings = list(getattr(review_result, "findings", []) or [])
    rejected = list(getattr(review_result, "rejected", []) or [])
    outputs = list(getattr(review_result, "outputs", []) or [])

    by_scope: dict[str, dict[str, Any]] = {}
    for item in outputs:
        scope = str(item.get("scope", "combined")) if isinstance(item, dict) else "combined"
        output = item.get("output", {}) if isinstance(item, dict) else {}
        if not isinstance(output, dict):
            output = {}
        by_scope.setdefault(scope, {"normalized_finding_codes": [], "review_status": output.get("review_status", "")})
        for finding in output.get("findings", []) or []:
            if isinstance(finding, dict):
                by_scope[scope]["normalized_finding_codes"].append(str(finding.get("code", "")))

    classifications = []
    for finding in findings:
        payload = finding.model_dump(mode="json") if hasattr(finding, "model_dump") else dict(finding)
        cls = classify_material_universe_finding(payload)
        classifications.append(
            {
                "finding_id": payload.get("finding_id", ""),
                "code": payload.get("code", ""),
                "severity": payload.get("severity", ""),
                "classification": cls.classification,
                "owner_route": cls.owner_route,
                "fail_closed": cls.fail_closed,
                "affected_patch_types": payload.get("affected_patch_types", []),
                "affected_json_paths": payload.get("affected_json_paths", []),
            }
        )

    rejected_by_code: dict[str, int] = {}
    for item in rejected:
        code = str(item.get("code", "")) if isinstance(item, dict) else ""
        rejected_by_code[code] = rejected_by_code.get(code, 0) + 1

    return {
        "scope_summary": by_scope,
        "classification_summary": classifications,
        "rejected_summary": rejected_by_code,
        "coverage_complete": bool(getattr(review_result, "coverage_complete", False)),
        "blocking_finding_count": sum(
            1
            for finding in findings
            if str(getattr(finding, "severity", "")).lower().endswith("error")
        ),
    }


__all__ = [
    "MaterialUniverseFindingClassification",
    "classify_material_universe_finding",
    "material_universe_finding_diagnostics",
]
