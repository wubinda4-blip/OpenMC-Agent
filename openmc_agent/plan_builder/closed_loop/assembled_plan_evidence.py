"""Assembled Plan evidence pack, contract matrix, and gate applicability."""

from __future__ import annotations

from typing import Any

from .assembled_plan_binding import build_assembled_plan_binding_view
from .fingerprints import compute_evidence_pack_hash
from .models import (
    AssembledPlanBindingView,
    AssembledPlanContractMatrix,
    AssembledPlanContractRow,
    AssembledPlanEvidencePack,
    PlanClosedLoopPolicy,
    PlanEvidenceItem,
    PlanGateId,
    PlanLoopMode,
    PlanReviewAction,
)


def assembled_plan_gate_applicable(state: Any) -> bool:
    """Gate applies when an assembled plan exists."""
    return getattr(state, "assembled_plan", None) is not None


def assembled_plan_gate_ready(state: Any) -> bool:
    """Controlled/advisory review requires an assembled SimulationPlan."""
    return assembled_plan_gate_applicable(state)


def assembled_plan_gate_input_hash(state: Any, *, policy: PlanClosedLoopPolicy | None = None) -> str:
    """Bind the gate input to every input that should invalidate the accepted hash."""
    plan = getattr(state, "assembled_plan", None)
    view = None
    if plan is not None:
        try:
            from openmc_agent.schemas import SimulationPlan
            if isinstance(plan, dict):
                plan_obj = SimulationPlan.model_validate(plan)
            else:
                plan_obj = plan
            view = build_assembled_plan_binding_view(state=state, plan=plan_obj)
        except Exception:
            pass
    payload: dict[str, Any] = {
        "assembled_plan_present": plan is not None,
        "accepted_gate_hashes": view.accepted_gate_hashes if view else {},
        "patch_hashes": view.patch_hashes if view else {},
        "canonical_task_plan_hash": view.canonical_task_plan_hash if view else "",
    }
    if policy is not None:
        payload["assembled_plan_review_mode"] = policy.assembled_plan_review_mode
        payload["review_schema_version"] = "1"
    return compute_evidence_pack_hash(payload)


def build_assembled_plan_contract_matrix(view: AssembledPlanBindingView, issues: list[dict[str, Any]] | None = None) -> AssembledPlanContractMatrix:
    """Construct the seven-kind contract matrix from the binding view."""
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for issue in issues or []:
        kind = str(issue.get("row_kind", "root_reachability"))
        key = str(issue.get("row_key", ""))
        by_key.setdefault((kind, key), []).append(issue)
    rows: list[AssembledPlanContractRow] = []

    def _codes(kind: str, key: str) -> list[str]:
        return sorted({str(item.get("code")) for item in by_key.get((kind, key), [])})

    # 1. Root selection row.
    root_status = "pass" if view.selected_roots else "fail"
    rows.append(AssembledPlanContractRow(
        row_id="rs:root", row_kind="root_selection",
        expected="unique root", actual=view.selected_renderer or "none",
        coverage_status=root_status,  # type: ignore[arg-type]
        issue_codes=_codes("root_selection", "root"),
    ))
    # 2. Root reachability row.
    unreachable_count = len(view.unreachable_required_ids)
    reach_status = "pass" if unreachable_count == 0 else "fail"
    rows.append(AssembledPlanContractRow(
        row_id="rr:reachability", row_kind="root_reachability",
        expected="all required reachable", actual=f"{unreachable_count} unreachable",
        coverage_status=reach_status,  # type: ignore[arg-type]
        issue_codes=_codes("root_reachability", "reachability"),
    ))
    # 3. Reference integrity row.
    ref_status = "pass" if not view.unresolved_references else "fail"
    rows.append(AssembledPlanContractRow(
        row_id="ri:references", row_kind="reference_integrity",
        actual=f"{len(view.unresolved_references)} unresolved",
        coverage_status=ref_status,  # type: ignore[arg-type]
        issue_codes=_codes("reference_integrity", "references"),
    ))
    # 4. Renderer capability row.
    rend_status = "pass" if view.selected_renderability in ("exportable", "runnable") else ("ambiguous" if view.selected_renderability == "skeleton" else "fail")
    rows.append(AssembledPlanContractRow(
        row_id="rc:renderer", row_kind="renderer_capability",
        renderer_name=view.selected_renderer,
        expected="exportable+", actual=view.selected_renderability,
        coverage_status=rend_status,  # type: ignore[arg-type]
        issue_codes=_codes("renderer_capability", "renderer"),
    ))
    # 5. Source feasibility row.
    src_status = "pass" if view.static_source_feasibility.feasible else "fail"
    rows.append(AssembledPlanContractRow(
        row_id="sf:source", row_kind="static_source_feasibility",
        expected="feasible", actual=view.static_source_feasibility.source_strategy,
        coverage_status=src_status,  # type: ignore[arg-type]
        issue_codes=list(view.static_source_feasibility.issue_codes),
    ))
    # 6. Plot coverage rows.
    for plot in view.plot_coverage_records:
        plot_status = "pass" if plot.within_domain and plot.positive_extent else "fail"
        rows.append(AssembledPlanContractRow(
            row_id=f"pc:{plot.plot_id}", row_kind="plot_coverage",
            object_id=plot.plot_id,
            coverage_status=plot_status,  # type: ignore[arg-type]
            issue_codes=list(plot.issue_codes),
        ))
    # 7. Execution check row.
    ec = view.execution_check_record
    ec_status = "pass" if ec.inactive_lt_batches and ec.within_smoke_limits and ec.consistent_with_renderability else "fail"
    rows.append(AssembledPlanContractRow(
        row_id="ec:execution_check", row_kind="execution_check",
        coverage_status=ec_status,  # type: ignore[arg-type]
        issue_codes=list(ec.issue_codes),
    ))
    matrix = AssembledPlanContractMatrix(rows=rows)
    matrix.input_hash = compute_evidence_pack_hash(matrix)
    return matrix


def build_assembled_plan_evidence_pack(
    *, state: Any, policy: PlanClosedLoopPolicy, plan: Any, deterministic_issues: list[dict[str, Any]] | None = None,
) -> AssembledPlanEvidencePack:
    view = build_assembled_plan_binding_view(state=state, plan=plan)
    matrix = build_assembled_plan_contract_matrix(view, deterministic_issues)
    items: list[PlanEvidenceItem] = []
    index = 1

    def _add(kind: str, prefix: str, patch_type: str | None, path: str | None, label: str, value: Any) -> None:
        nonlocal index
        canonical_hash = compute_evidence_pack_hash({"kind": kind, "patch_type": patch_type, "path": path, "value": value})
        items.append(PlanEvidenceItem(ref_id=f"{prefix}{index:03d}", evidence_kind=kind, patch_type=patch_type, json_path=path, label=label, value=value, canonical_hash=canonical_hash))
        index += 1

    _add("accepted_fact_contract", "G", None, "/object_graph", "assembled object graph", view.object_graph.model_dump(mode="json"))
    _add("accepted_fact_contract", "R", None, "/reachability", "root reachability records", [r.model_dump(mode="json") for r in view.reachability_records])
    _add("patch_fragment", "CM", None, "/renderer_matrix", "renderer capability matrix", [r.model_dump(mode="json") for r in view.renderer_capability_matrix])
    _add("patch_fragment", "SF", None, "/source_feasibility", "static source feasibility", view.static_source_feasibility.model_dump(mode="json"))
    _add("patch_fragment", "EC", None, "/execution_check", "execution check record", view.execution_check_record.model_dump(mode="json"))
    for issue in deterministic_issues or []:
        _add("deterministic_issue", "D", None, None, f"deterministic issue {issue.get('code', '')}", issue)
    pack = AssembledPlanEvidencePack(
        binding_view=view,
        contract_matrix=matrix,
        deterministic_issues=list(deterministic_issues or []),
        relevant_patch_hashes=view.patch_hashes,
        accepted_facts_hash=view.accepted_gate_hashes.get("facts", ""),
        evidence_items=items,
        confirmed_records=[item.model_dump(mode="json") for item in getattr(state, "plan_confirmed_plan_fact_records", {}).values()],
        allowed_actions=list(_allowed_review_actions(policy)),
    )
    pack.input_hash = assembled_plan_gate_input_hash(state, policy=policy)
    pack.evidence_pack_id = pack.input_hash
    return pack


def _allowed_review_actions(policy: PlanClosedLoopPolicy) -> list[PlanReviewAction]:
    if policy.mode is PlanLoopMode.OFF:
        return []
    return [PlanReviewAction.APPROVE, PlanReviewAction.REVISE_CURRENT_PATCH, PlanReviewAction.RETRY_DEPENDENCY, PlanReviewAction.ASK_HUMAN, PlanReviewAction.FAIL_CLOSED]


__all__ = [
    "assembled_plan_gate_applicable",
    "assembled_plan_gate_ready",
    "assembled_plan_gate_input_hash",
    "build_assembled_plan_contract_matrix",
    "build_assembled_plan_evidence_pack",
]
