"""Axial Geometry critic prompt builder."""

from __future__ import annotations

from typing import Any


def build_axial_geometry_review_prompt(evidence_pack: Any) -> str:
    """Build the critic prompt from the evidence pack."""
    view = evidence_pack.binding_view
    matrix = evidence_pack.contract_matrix
    lines: list[str] = []
    lines.append("# Axial Geometry Review")
    lines.append("")
    lines.append("You are an independent reviewer for the Axial Geometry Gate.")
    lines.append("Your job is to check whether source axial semantics are faithfully")
    lines.append("represented by the axial patches.  You MUST NOT edit patches, decide")
    lines.append("owners, decide actions, recompute interval intersections, recompute")
    lines.append("segments, decide numerical tolerances, or claim final root reachability.")
    lines.append("")
    lines.append("## Evidence reference legend")
    lines.append("- F#: accepted Facts / source axial contract")
    lines.append("- M#: Material-Universe accepted contract")
    lines.append("- P#: Placement accepted contract")
    lines.append("- B#: base-path profile / segment")
    lines.append("- A#: axial layer")
    lines.append("- L#: lattice loading")
    lines.append("- O#: overlay")
    lines.append("- I#: localized insert axial record")
    lines.append("- T#: through-path record")
    lines.append("- G#: derived segment")
    lines.append("- D#: deterministic issue")
    lines.append("")
    domain = view.axial_domain_cm if view else None
    af = view.active_fuel_region_cm if view else None
    lines.append(f"axial_domain_cm: {domain}")
    lines.append(f"active_fuel_region_cm: {af}")
    lines.append(f"layers: {len(view.axial_layer_records) if view else 0}")
    lines.append(f"overlays: {len(view.axial_overlay_records) if view else 0}")
    lines.append(f"loadings: {len(view.lattice_loading_records) if view else 0}")
    lines.append(f"profiles: {len(view.base_path_profile_records) if view else 0}")
    lines.append(f"localized inserts: {len(view.localized_insert_axial_records) if view else 0}")
    lines.append(f"derived segments: {len(view.derived_segments) if view else 0}")
    lines.append("")
    lines.append("## Contract matrix rows")
    for row in matrix.rows if matrix else []:
        lines.append(f"- {row.row_id} [{row.row_kind}] coverage={row.coverage_status} issues={row.issue_codes}")
    lines.append("")
    lines.append("## Deterministic issues")
    det = evidence_pack.deterministic_issues if evidence_pack else []
    if det:
        for issue in det:
            lines.append(f"- D: {issue.get('code', '')} [{issue.get('severity', '')}] {issue.get('message', '')}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## Instructions")
    lines.append("1. Check that source axial domain and active-fuel region are covered.")
    lines.append("2. Check that thin structures (spacer grids, plates) are expressed as overlays, not entire layer replacements.")
    lines.append("3. Check that base-path component ordering and segment semantics match source.")
    lines.append("4. Check that localized insert anchors and control states are semantically correct.")
    lines.append("5. Check that homogenization and clipping are source-approved.")
    lines.append("6. Declare root-reachability claims ONLY for static segment occupancy, not OpenMC root reachability.")
    lines.append("7. Output findings with evidence_refs from the legend above.  Unknown refs will be rejected.")
    lines.append("8. Coverage: review ALL contract rows, layers, overlays, loadings, profiles, and inserts.")
    lines.append("")
    import json
    lines.append("## Output format")
    lines.append("Output a single JSON object matching AxialGeometryReviewModelOutput.")
    lines.append("```json")
    lines.append(json.dumps({"review_status": "complete", "findings": [], "reviewed_contract_row_ids": [], "reviewed_evidence_refs": [], "coverage_summary": {}, "concise_summary": ""}, indent=2))
    lines.append("```")
    return "\n".join(lines)


def build_axial_geometry_review_schema_retry_prompt(evidence_pack: Any, error: str, raw: str) -> str:
    return (
        f"The previous output was not valid JSON matching AxialGeometryReviewModelOutput.\n"
        f"Error: {error}\n\n"
        f"Please output a single valid JSON object.  Do not include prose outside the JSON.\n\n"
        f"{build_axial_geometry_review_prompt(evidence_pack)}"
    )


__all__ = [
    "build_axial_geometry_review_prompt",
    "build_axial_geometry_review_schema_retry_prompt",
]
