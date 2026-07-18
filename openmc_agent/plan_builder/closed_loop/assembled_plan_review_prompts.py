"""Assembled Plan critic prompt builder."""

from __future__ import annotations

from typing import Any


def build_assembled_plan_review_prompt(evidence_pack: Any) -> str:
    view = evidence_pack.binding_view
    matrix = evidence_pack.contract_matrix
    lines: list[str] = []
    lines.append("# Assembled Plan Review")
    lines.append("")
    lines.append("You are an independent reviewer for the Final / Assembled Plan Gate.")
    lines.append("Your job is to check whether the assembled SimulationPlan is internally")
    lines.append("consistent, root-reachable, renderer-capable, and statically source-feasible.")
    lines.append("You MUST NOT edit patches, decide owners, decide actions, choose renderers,")
    lines.append("or claim OpenMC runtime correctness, keff convergence, or source rejection")
    lines.append("fraction guarantees.")
    lines.append("")
    lines.append("## Evidence reference legend")
    lines.append("- G#: assembled object graph")
    lines.append("- R#: root reachability records")
    lines.append("- CM#: renderer capability matrix")
    lines.append("- SF#: static source feasibility")
    lines.append("- EC#: execution check record")
    lines.append("- D#: deterministic issue")
    lines.append("")
    if view:
        lines.append(f"model_kind: {view.model_kind}")
        lines.append(f"object_count: {len(view.object_graph.nodes)}")
        lines.append(f"selected_roots: {view.selected_roots}")
        lines.append(f"unreachable_required: {len(view.unreachable_required_ids)}")
        lines.append(f"selected_renderer: {view.selected_renderer}")
        lines.append(f"selected_renderability: {view.selected_renderability}")
        lines.append(f"source_strategy: {view.static_source_feasibility.source_strategy}")
        lines.append(f"source_feasible: {view.static_source_feasibility.feasible}")
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
    lines.append("1. Verify that root selection is unique and semantically correct.")
    lines.append("2. Verify that required objects are root-reachable.")
    lines.append("3. Verify that renderer capability meets the required level.")
    lines.append("4. Verify that static source strategy is feasible (without claiming runtime success).")
    lines.append("5. Verify that plots and execution check are coherent with renderability.")
    lines.append("6. Check for homogenization/approximation disclosure.")
    lines.append("7. Output findings with evidence_refs from the legend above.")
    lines.append("8. Coverage: review ALL contract rows and renderers.")
    lines.append("")
    import json
    lines.append("## Output format")
    lines.append("Output a single JSON object matching AssembledPlanReviewModelOutput.")
    lines.append("```json")
    lines.append(json.dumps({"review_status": "complete", "findings": [], "reviewed_contract_row_ids": [], "reviewed_evidence_refs": [], "coverage_summary": {}, "concise_summary": ""}, indent=2))
    lines.append("```")
    return "\n".join(lines)


def build_assembled_plan_review_schema_retry_prompt(evidence_pack: Any, error: str, raw: str) -> str:
    return (
        f"The previous output was not valid JSON matching AssembledPlanReviewModelOutput.\n"
        f"Error: {error}\n\n"
        f"Please output a single valid JSON object.\n\n"
        f"{build_assembled_plan_review_prompt(evidence_pack)}"
    )


__all__ = [
    "build_assembled_plan_review_prompt",
    "build_assembled_plan_review_schema_retry_prompt",
]
