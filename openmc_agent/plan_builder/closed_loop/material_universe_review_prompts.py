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
            "Coverage must be complete: every material_id, universe_id, contract_row_id must be reviewed.",
        ],
    }
    return "Review the Material-Universe binding below.\nINPUT:\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def build_material_universe_review_schema_retry_prompt(pack: Any, error: str, raw: str) -> str:
    return (
        f"The previous response was not schema-valid: {error}\n"
        f"Raw output (truncated): {raw[:500]}\n\n"
        "Return a single JSON object matching MaterialUniverseReviewModelOutput. "
        "Do not include prose outside the JSON object."
    )


__all__ = ["build_material_universe_review_prompt", "build_material_universe_review_schema_retry_prompt"]
