"""Deterministic Assembled Plan preflight.

Reuses assembler diagnostics, validate_simulation_plan, renderer capability
reports, and the binding view's object graph / reachability analysis.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .assembled_plan_binding import build_assembled_plan_binding_view
from .assembled_plan_evidence import assembled_plan_gate_input_hash, build_assembled_plan_contract_matrix
from .models import AssembledPlanBindingView, PlanClosedLoopPolicy


class AssembledPlanPreflightResult(AgentBaseModel):
    ok: bool = False
    binding_view: AssembledPlanBindingView | None = None
    issues: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    input_hash: str = ""

    @property
    def blocking_issues(self) -> list[dict[str, Any]]:
        return [item for item in self.issues if item.get("severity") == "error"]


def _issue(code: str, message: str, *, severity: str = "error", row_kind: str = "root_reachability", row_key: str = "", **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "severity": severity, "blocking": severity == "error", "message": message, "row_kind": row_kind, "row_key": row_key}
    payload.update({k: v for k, v in extra.items() if v is not None})
    return payload


def _collect_root_issues(view: AssembledPlanBindingView) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not view.selected_roots:
        issues.append(_issue("assembled.root_missing", "no root selected", row_kind="root_selection", row_key="root"))
    if len(view.selected_roots) > 1 and view.model_kind not in ("core", "mixed"):
        issues.append(_issue("assembled.root_ambiguous", f"multiple roots selected: {view.selected_roots}", row_kind="root_selection", row_key="root", severity="warning"))
    for cycle in view.cycles:
        issues.append(_issue("assembled.reference_cycle", f"cycle detected: {' -> '.join(cycle)}", row_kind="reference_integrity", row_key=":".join(cycle)))
    return issues


def _collect_reachability_issues(view: AssembledPlanBindingView) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for uid in view.unreachable_required_ids:
        rec = next((r for r in view.reachability_records if r.object_id == uid), None)
        kind = rec.object_kind if rec else "unknown"
        issues.append(_issue(f"assembled.required_{kind}_unreachable" if kind != "unknown" else "assembled.unresolved_reference",
                             f"required {kind} {uid} unreachable from root", row_kind="root_reachability", row_key=uid))
    return issues


def _collect_renderer_issues(view: AssembledPlanBindingView, policy: PlanClosedLoopPolicy) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    required_level = str(policy.metadata.get("assembled_plan_required_renderability", "exportable"))
    levels = {"none": 0, "skeleton": 1, "exportable": 2, "runnable": 3}
    actual_rank = levels.get(view.selected_renderability, 0)
    required_rank = levels.get(required_level, 2)
    if not view.selected_renderer:
        issues.append(_issue("assembled.renderer_none", "no renderer selected", row_kind="renderer_capability", row_key="renderer"))
    elif view.selected_renderability == "skeleton" and required_level != "skeleton":
        issues.append(_issue("assembled.renderer_skeleton_only", f"only skeleton renderer available (required={required_level})", row_kind="renderer_capability", row_key="renderer"))
    elif actual_rank < required_rank:
        issues.append(_issue("assembled.renderer_below_required_level", f"renderability {view.selected_renderability} below required {required_level}", row_kind="renderer_capability", row_key="renderer"))
    # Collect structured issues from renderer capability report.
    for row in view.renderer_capability_matrix:
        for code in row.structured_issue_codes:
            if row.renderer_name == view.selected_renderer:
                issues.append(_issue(f"assembled.renderer_issue:{code}", f"renderer {row.renderer_name}: {code}", row_kind="renderer_capability", row_key=row.renderer_name, severity="warning"))
    return issues


def _collect_source_issues(view: AssembledPlanBindingView) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for code in view.static_source_feasibility.issue_codes:
        issues.append(_issue(code, f"source feasibility: {code}", row_kind="static_source_feasibility", row_key="source"))
    return issues


def _collect_plot_exec_issues(view: AssembledPlanBindingView) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for plot in view.plot_coverage_records:
        for code in plot.issue_codes:
            issues.append(_issue(code, f"plot {plot.plot_id}: {code}", row_kind="plot_coverage", row_key=plot.plot_id))
    for code in view.execution_check_record.issue_codes:
        issues.append(_issue(code, f"execution check: {code}", row_kind="execution_check", row_key="execution_check"))
    return issues


def _collect_assembler_issues(state: Any) -> list[dict[str, Any]]:
    """Reuse assembler diagnostics already stored in state.validation_issues."""
    issues: list[dict[str, Any]] = []
    for item in getattr(state, "validation_issues", []):
        if isinstance(item, dict):
            code = str(item.get("code", ""))
            severity = str(item.get("severity", "error"))
        else:
            code = str(getattr(item, "code", ""))
            severity = str(getattr(item, "severity", "error"))
        if not code:
            continue
        mapped = _map_assembler_code(code)
        if mapped is None:
            continue
        issues.append(_issue(mapped, code, severity=severity, row_kind=_row_kind(mapped), row_key=mapped))
    return issues


_ASSEMBLER_CODE_MAP: dict[str, str] = {
    "assembly.unresolved_material_reference": "assembled.unresolved_reference",
    "assembly.unresolved_universe_reference": "assembled.unresolved_reference",
    "assembly.missing_patch": "assembled.required_object_missing",
    "assembly.simulation_plan_schema_invalid": "assembled.reference_integrity",
    "assembly.pin_map.default_universe_missing": "assembled.required_universe_unreachable",
    "assembly.requires_lattice": "assembled.root_lattice_missing",
    "assembly.requires_assembly_spec": "assembled.root_assembly_missing",
    "core.lattice_ref_missing": "assembled.root_lattice_missing",
    "core.requires_lattice": "assembled.root_lattice_missing",
}


def _map_assembler_code(code: str) -> str | None:
    return _ASSEMBLER_CODE_MAP.get(code)


def _row_kind(code: str) -> str:
    if "root" in code and "reach" not in code:
        return "root_selection"
    if "unreachable" in code or "reach" in code:
        return "root_reachability"
    if "reference" in code or "unresolved" in code:
        return "reference_integrity"
    if "renderer" in code:
        return "renderer_capability"
    if "source" in code:
        return "static_source_feasibility"
    if "plot" in code:
        return "plot_coverage"
    if "execution" in code or "smoke" in code:
        return "execution_check"
    return "reference_integrity"


def run_assembled_plan_preflight(*, state: Any, policy: PlanClosedLoopPolicy, plan: Any) -> AssembledPlanPreflightResult:
    """Run deterministic preflight on the assembled plan."""
    view = build_assembled_plan_binding_view(state=state, plan=plan)
    issues: list[dict[str, Any]] = []
    issues.extend(_collect_root_issues(view))
    issues.extend(_collect_reachability_issues(view))
    issues.extend(_collect_renderer_issues(view, policy))
    issues.extend(_collect_source_issues(view))
    issues.extend(_collect_plot_exec_issues(view))
    issues.extend(_collect_assembler_issues(state))
    # Deduplicate.
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for issue in issues:
        key = (issue["code"], issue.get("row_key", ""))
        if key not in seen or (issue.get("severity") == "error" and seen[key].get("severity") != "error"):
            seen[key] = issue
    issues = list(seen.values())
    matrix = build_assembled_plan_contract_matrix(view, issues)
    input_hash = assembled_plan_gate_input_hash(state, policy=policy)
    blocking = [i for i in issues if i.get("severity") == "error"]
    return AssembledPlanPreflightResult(
        ok=len(blocking) == 0,
        binding_view=view,
        issues=issues,
        summary={
            "total": len(issues),
            "blocking": len(blocking),
            "object_count": len(view.object_graph.nodes),
            "edge_count": len(view.object_graph.edges),
            "unreachable_count": len(view.unreachable_required_ids),
            "renderer_count": len(view.renderer_capability_matrix),
            "selected_renderer": view.selected_renderer,
            "selected_renderability": view.selected_renderability,
            "matrix_rows": len(matrix.rows),
        },
        input_hash=input_hash,
    )


__all__ = ["AssembledPlanPreflightResult", "run_assembled_plan_preflight"]
