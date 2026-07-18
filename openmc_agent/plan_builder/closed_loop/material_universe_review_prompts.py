"""Prompt builder for the Material-Universe Critic."""

from __future__ import annotations

import json
from typing import Any


def build_material_universe_review_prompt(pack: Any) -> str:
    """Build the Critic prompt from a MaterialUniverseEvidencePack.

    The prompt is deliberately bounded: large patches are represented by
    compact summaries and contract rows, not full patch JSON.
    """
    view = pack.binding_view
    matrix = pack.contract_matrix
    # Compact summaries — never the full patch content.
    material_summaries = [
        {"material_id": m.material_id, "role": m.role, "density_status": m.density_status, "composition_status": m.composition_status, "source_variant_id": m.source_variant_id, "resolver_status": m.resolver_status}
        for m in view.material_records
    ]
    universe_summaries = [
        {"universe_id": u.universe_id, "kind": u.kind, "fuel_variant_id": u.fuel_variant_id, "material_ids": u.material_ids, "cell_roles": u.cell_roles}
        for u in view.universe_records
    ]
    variant_summaries = [
        {"variant_id": v.variant_id, "material_id": v.material_id, "active_fuel_universe_ids": v.active_fuel_universe_ids, "status": v.status}
        for v in view.fuel_variant_bindings
    ]
    contract_rows = [
        {"row_id": r.row_id, "row_kind": r.row_kind, "coverage_status": r.coverage_status, "issue_codes": r.issue_codes, "material_id": r.material_id, "universe_id": r.universe_id, "variant_id": r.variant_id}
        for r in matrix.rows
    ]
    deterministic = [{"code": i.get("code"), "severity": i.get("severity"), "message": i.get("message"), "row_kind": i.get("row_kind")} for i in pack.deterministic_issues]
    payload = {
        "gate_id": "material_universe",
        "input_hash": pack.input_hash,
        "planning_scope": view.planning_scope,
        "required_material_contracts": view.required_material_contracts,
        "material_summaries": material_summaries,
        "universe_summaries": universe_summaries,
        "fuel_variant_summaries": variant_summaries,
        "contract_matrix_rows": contract_rows,
        "deterministic_issues": deterministic,
        "material_species_report_summary": {
            mid: {"status": info.get("status"), "warnings": info.get("warnings", [])}
            for mid, info in (pack.material_species_report.get("materials", {}) if isinstance(pack.material_species_report, dict) else {}).items()
        },
        "evidence_ref_legend": {
            "F": "accepted Facts source contract",
            "M": "material record",
            "U": "universe record",
            "C": "cell binding",
            "V": "fuel variant row",
            "D": "deterministic issue",
        },
        "instructions": [
            "Review ONLY the Material → Universe static edge.",
            "Do NOT edit patches, output JSON Patch, decide owner, or decide retry action.",
            "Do NOT recompute numerical values; the deterministic preflight already did.",
            "Do NOT claim final root reachability — only static material-universe binding.",
            "Each finding must reference existing evidence_refs and contract_row_ids.",
            "Flag only semantic issues Python cannot determine deterministically.",
            "Do NOT restate or duplicate deterministic_issues as findings; Python already owns those findings.",
            "If there are no additional semantic issues, return findings=[].",
            "Coverage must be complete: every material_id, universe_id, contract_row_id must be reviewed.",
            "Set review_status to exactly 'complete' after completing that coverage; do not use 'incomplete'.",
            "Each finding confidence is a JSON number from 0.0 to 1.0, never a label such as high, medium, or low.",
            "Each finding object must use exactly these field names: code, severity, category, message, evidence_refs, contract_row_ids, affected_json_paths, repairable_by_llm, requires_human, confidence, expected_semantics, current_semantics, metadata.",
            "Do not use aliases such as finding_id or description.",
        ],
        "required_output_shape": {
            "review_status": "complete",
            "findings": [
                {
                    "code": "string",
                    "severity": "error|warning|info",
                    "category": "source_coverage|unsupported_inference|cross_patch_mismatch|placement_gap|reachability_gap|physical_ambiguity|representation_error|schema_or_format|no_progress|budget_exhausted",
                    "message": "string",
                    "evidence_refs": ["existing evidence ref"],
                    "contract_row_ids": ["existing row id"],
                    "affected_json_paths": [],
                    "repairable_by_llm": False,
                    "requires_human": False,
                    "confidence": 0.9,
                    "expected_semantics": None,
                    "current_semantics": None,
                    "metadata": {},
                }
            ],
            "reviewed_contract_row_ids": ["every contract row id"],
            "reviewed_evidence_refs": ["every reviewed evidence ref"],
            "coverage_summary": {
                "reviewed_source_requirement_ids": [],
                "reviewed_material_ids": ["every material id"],
                "reviewed_universe_ids": ["every universe id"],
                "reviewed_contract_row_ids": ["every contract row id"],
                "reviewed_evidence_refs": ["every reviewed evidence ref"],
                "omitted_material_count": 0,
                "omitted_universe_count": 0,
                "omitted_contract_row_count": 0,
                "unresolved_evidence_count": 0,
            },
            "concise_summary": "string",
        },
    }
    return "Review the Material-Universe binding below.\nINPUT:\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def build_material_universe_review_schema_retry_prompt(pack: Any, error: str, raw: str) -> str:
    return (
        f"The previous response was not schema-valid: {error}\n"
        f"Raw output (truncated): {raw[:500]}\n\n"
        "Return a single JSON object matching MaterialUniverseReviewModelOutput. "
        "Do not include prose outside the JSON object. Use review_status='complete' "
        "after reviewing all IDs. Use numeric confidence values in [0.0, 1.0], "
        "not high/medium/low strings. Do not repeat deterministic_issues as "
        "findings; return findings=[] when there are no additional semantic issues. "
        "You MUST still populate reviewed_contract_row_ids and all four coverage_summary "
        "reviewed_* lists from the original input.\n\n"
        "Original review input (use this to produce the required coverage lists):\n"
        + build_material_universe_review_prompt(pack)
    )


__all__ = ["build_material_universe_review_prompt", "build_material_universe_review_schema_retry_prompt"]
