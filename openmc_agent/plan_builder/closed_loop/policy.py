"""Deterministic reactor-neutral gate and action policies."""

from __future__ import annotations

from typing import Any

from .models import (
    PlanClosedLoopPolicy, PlanFindingSeverity, PlanGateId, PlanLoopMode,
    PlanReviewAction, PlanStageState,
)

_GATES: dict[PlanGateId, tuple[str, ...]] = {
    PlanGateId.FACTS: ("facts",),
    PlanGateId.MATERIAL_UNIVERSE: ("materials", "universes"),
    PlanGateId.PLACEMENT: ("localized_insert_profiles", "pin_map", "assembly_catalog", "core_layout"),
    PlanGateId.AXIAL_GEOMETRY: ("base_path_axial_profiles", "axial_layers", "axial_overlays"),
    PlanGateId.ASSEMBLED_PLAN: (),
}
_ORDER = tuple(_GATES)
_PRIMARY_GATE = {ptype: gate for gate, patches in _GATES.items() for ptype in patches}


def canonical_gate_order() -> list[PlanGateId]:
    return list(_ORDER)


def gate_definition(gate_id: PlanGateId) -> dict[str, Any]:
    return {"gate_id": gate_id.value, "patch_types": list(_GATES[gate_id])}


def enabled_gates(policy: PlanClosedLoopPolicy) -> list[PlanGateId]:
    if policy.mode is PlanLoopMode.OFF:
        return []
    return [gate for gate in _ORDER if policy.gate_enabled.get(gate, False)]


def enabled_gates_through(target_gate: PlanGateId | str) -> list[PlanGateId]:
    """Return the cumulative gate prefix ending at ``target_gate``.

    Phase 8A Step 6 contract: ``--stop-after-gate`` is a cumulative
    prefix, not an exact set.  Stopping at ``material_universe`` enables
    both ``facts`` and ``material_universe`` so the Facts Gate still
    runs as a barrier.  Stopping at ``placement`` enables facts +
    material_universe + placement.  Stopping at ``assembled_plan``
    enables all five gates.

    Reactor-neutral: the order is the canonical
    :data:`_ORDER` (facts → material_universe → placement →
    axial_geometry → assembled_plan), independent of any reactor type.
    """

    target_value = target_gate.value if isinstance(target_gate, PlanGateId) else str(target_gate).lower()
    prefix: list[PlanGateId] = []
    for gate in _ORDER:
        prefix.append(gate)
        if gate.value == target_value:
            return prefix
    # Unknown target → enable everything (defensive; tests cover the
    # known-target path).
    return list(_ORDER)


def patch_types_for_gate(gate_id: PlanGateId, required_patch_types: list[str] | None = None) -> list[str]:
    candidates = list(_GATES[gate_id])
    if required_patch_types is None:
        return candidates
    required = set(required_patch_types)
    return [patch_type for patch_type in candidates if patch_type in required]


def gate_for_patch_type(patch_type: str) -> PlanGateId | None:
    return _PRIMARY_GATE.get(patch_type)


def _is_blocking_issue(issue: dict[str, Any]) -> bool:
    return bool(issue.get("blocking")) or issue.get("severity") == "error"


def compute_allowed_actions(
    *, policy: PlanClosedLoopPolicy, stage_state: PlanStageState,
    findings: list[Any], deterministic_issues: list[dict[str, Any]],
    additional_llm_calls_used: int = 0,
) -> list[PlanReviewAction]:
    if policy.mode is PlanLoopMode.OFF:
        return []
    exhausted = (
        additional_llm_calls_used >= policy.max_total_additional_llm_calls
        or stage_state.review_count >= policy.max_review_rounds_per_gate
        or stage_state.repair_count >= policy.max_repair_rounds_per_gate
        or stage_state.human_round_count >= policy.max_human_rounds_per_gate
        or stage_state.no_progress_count >= policy.max_no_progress_rounds
    )
    if exhausted:
        return [PlanReviewAction.FAIL_CLOSED]
    error_findings = [item for item in findings if getattr(item, "severity", None) == PlanFindingSeverity.ERROR or (isinstance(item, dict) and item.get("severity") == "error")]
    human_required = any(bool(getattr(item, "requires_human", False) if not isinstance(item, dict) else item.get("requires_human")) for item in error_findings)
    if human_required:
        return [PlanReviewAction.ASK_HUMAN, PlanReviewAction.FAIL_CLOSED] if policy.enable_human_gate else [PlanReviewAction.FAIL_CLOSED]
    if not error_findings and not any(_is_blocking_issue(issue) for issue in deterministic_issues):
        return [PlanReviewAction.APPROVE]
    repairable = any(bool(getattr(item, "repairable_by_llm", False) if not isinstance(item, dict) else item.get("repairable_by_llm")) for item in error_findings)
    if repairable:
        return [PlanReviewAction.REVISE_CURRENT_PATCH, PlanReviewAction.RETRY_DEPENDENCY]
    return [PlanReviewAction.FAIL_CLOSED]
