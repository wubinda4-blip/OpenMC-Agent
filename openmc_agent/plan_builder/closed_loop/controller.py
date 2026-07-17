"""Deterministic state transitions and durable no-progress bookkeeping."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import (
    PlanClosedLoopPolicy, PlanGateId, PlanLoopMode, PlanLoopOutcome,
    PlanReviewDecision, PlanReviewFinding, PlanStageState, PlanStageStatus,
)
from .policy import canonical_gate_order, enabled_gates, patch_types_for_gate
from .models import PLAN_CLOSED_LOOP_CONTRACT_VERSION


class InvalidPlanLoopTransition(ValueError):
    def __init__(self, stage: PlanStageState, target: PlanStageStatus, allowed: set[PlanStageStatus]):
        super().__init__(
            f"invalid plan-loop transition stage_id={stage.stage_id} "
            f"from={stage.status.value} to={target.value} "
            f"allowed={[item.value for item in sorted(allowed, key=lambda x: x.value)]}"
        )


_ALLOWED: dict[PlanStageStatus, set[PlanStageStatus]] = {
    PlanStageStatus.PENDING: {PlanStageStatus.PROPOSING, PlanStageStatus.SKIPPED, PlanStageStatus.BLOCKED},
    PlanStageStatus.PROPOSING: {PlanStageStatus.VALIDATING, PlanStageStatus.BLOCKED},
    PlanStageStatus.VALIDATING: {PlanStageStatus.REVIEWING, PlanStageStatus.BLOCKED},
    PlanStageStatus.REVIEWING: {PlanStageStatus.REVIEWED, PlanStageStatus.REVIEW_FAILED, PlanStageStatus.ACCEPTED, PlanStageStatus.REPAIRING, PlanStageStatus.AWAITING_HUMAN, PlanStageStatus.BLOCKED},
    # Regeneration after a typed human fact confirmation starts a fresh facts
    # proposal; revision candidates still go directly to validation.
    PlanStageStatus.REPAIRING: {PlanStageStatus.PROPOSING, PlanStageStatus.VALIDATING, PlanStageStatus.BLOCKED},
    PlanStageStatus.AWAITING_HUMAN: {PlanStageStatus.REPAIRING, PlanStageStatus.BLOCKED},
    PlanStageStatus.ACCEPTED: set(),
    PlanStageStatus.BLOCKED: set(),
    PlanStageStatus.SKIPPED: set(),
    PlanStageStatus.REVIEWED: set(),
    PlanStageStatus.REVIEW_FAILED: set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize_gate_stage(gate_id: PlanGateId, required_patch_types: list[str]) -> PlanStageState:
    return PlanStageState(
        stage_id=f"plan_gate_{gate_id.value}", gate_id=gate_id,
        patch_types=patch_types_for_gate(gate_id, required_patch_types),
    )


def initialize_plan_loop_state(state: Any, policy: PlanClosedLoopPolicy, required_patch_types: list[str]) -> list[PlanStageState]:
    if policy.mode is PlanLoopMode.OFF:
        return []
    previous_contract = getattr(state, "plan_loop_contract_version", "0.1")
    state.plan_loop_mode = policy.mode
    state.plan_loop_policy = policy.model_dump(mode="json")
    state.plan_loop_contract_version = PLAN_CLOSED_LOOP_CONTRACT_VERSION
    if previous_contract != PLAN_CLOSED_LOOP_CONTRACT_VERSION and hasattr(state, "add_event"):
        state.add_event(
            "planning.retry_protocol_migrated_to_0_5",
            "plan closed-loop retry protocol migrated without clearing ledgers",
            {"from_contract": previous_contract, "to_contract": PLAN_CLOSED_LOOP_CONTRACT_VERSION},
        )
    # Foundation-only stages were never reviewed.  Upgrade them lazily so a
    # restored checkpoint is eligible for the first real gate run.  Never
    # reset a real reviewed/accepted/failed history.
    for gate_id in (PlanGateId.FACTS, PlanGateId.PLACEMENT):
        legacy = state.plan_loop_stages.get(f"plan_gate_{gate_id.value}")
        if legacy and legacy.status is PlanStageStatus.SKIPPED and legacy.metadata.get("review_not_implemented"):
            legacy.status = PlanStageStatus.PENDING
            legacy.completed_at = None
            legacy.metadata["review_not_implemented"] = False
            legacy.metadata["migrated_from_contract"] = "0.1_or_0.2_foundation_only"
            if gate_id is PlanGateId.PLACEMENT and hasattr(state, "add_event"):
                state.add_event(
                    "planning.placement_gate_migrated_to_0_4",
                    "foundation-only placement stage migrated to pending",
                    {"stage_id": legacy.stage_id},
                )
    created: list[PlanStageState] = []
    for gate_id in enabled_gates(policy):
        stage_id = f"plan_gate_{gate_id.value}"
        if stage_id not in state.plan_loop_stages:
            stage = initialize_gate_stage(gate_id, required_patch_types)
            state.plan_loop_stages[stage_id] = stage
            created.append(stage)
    return created


def transition_stage(stage: PlanStageState, target: PlanStageStatus) -> PlanStageState:
    allowed = _ALLOWED[stage.status]
    if target not in allowed:
        raise InvalidPlanLoopTransition(stage, target, allowed)
    now = _now()
    if stage.started_at is None:
        stage.started_at = now
    stage.status = target
    stage.updated_at = now
    if target in {PlanStageStatus.ACCEPTED, PlanStageStatus.BLOCKED, PlanStageStatus.SKIPPED, PlanStageStatus.REVIEWED, PlanStageStatus.REVIEW_FAILED}:
        stage.completed_at = now
    return stage


def record_findings(state: Any, stage: PlanStageState, findings: list[PlanReviewFinding]) -> None:
    for finding in findings:
        state.plan_review_findings[finding.finding_id] = finding
        if finding.finding_id not in stage.finding_ids:
            stage.finding_ids.append(finding.finding_id)
        if hasattr(state, "add_event"):
            state.add_event(
                "planning.review_finding_recorded", "plan review finding recorded",
                {"finding_id": finding.finding_id, "gate_id": finding.gate_id.value},
            )


def record_decision(state: Any, stage: PlanStageState, decision: PlanReviewDecision) -> None:
    state.plan_review_decisions[decision.decision_id] = decision
    if decision.decision_id not in stage.decision_ids:
        stage.decision_ids.append(decision.decision_id)
    if hasattr(state, "add_event"):
        state.add_event(
            "planning.review_decision_recorded", "plan review decision recorded",
            {"decision_id": decision.decision_id, "gate_id": decision.gate_id.value},
        )


def record_candidate(state: Any, stage: PlanStageState, issue_fingerprint: str, candidate_hash: str) -> bool:
    history = state.plan_loop_candidate_hashes_by_fingerprint.setdefault(issue_fingerprint, [])
    duplicate = candidate_hash in history
    history.append(candidate_hash)
    state.plan_loop_issue_attempts_by_fingerprint[issue_fingerprint] = state.plan_loop_issue_attempts_by_fingerprint.get(issue_fingerprint, 0) + 1
    stage.issue_fingerprint = issue_fingerprint
    stage.latest_candidate_hash = candidate_hash
    return duplicate


def record_no_progress(state: Any, stage: PlanStageState, issue_fingerprint: str, candidate_hash: str) -> bool:
    duplicate = record_candidate(state, stage, issue_fingerprint, candidate_hash)
    if duplicate:
        stage.no_progress_count += 1
        state.plan_loop_no_progress_events.append({
            "stage_id": stage.stage_id, "issue_fingerprint": issue_fingerprint,
            "candidate_hash": candidate_hash, "count": stage.no_progress_count,
        })
        if hasattr(state, "add_event"):
            state.add_event(
                "planning.no_progress_detected", "duplicate candidate recorded for issue fingerprint",
                {"stage_id": stage.stage_id, "issue_fingerprint": issue_fingerprint},
            )
    return duplicate


def check_stage_budget(state: Any, stage: PlanStageState, policy: PlanClosedLoopPolicy) -> str | None:
    if state.plan_loop_additional_llm_calls >= policy.max_total_additional_llm_calls:
        return "additional_llm_calls"
    if stage.review_count >= policy.max_review_rounds_per_gate:
        return "review_rounds"
    if stage.repair_count >= policy.max_repair_rounds_per_gate:
        return "repair_rounds"
    if stage.human_round_count >= policy.max_human_rounds_per_gate:
        return "human_rounds"
    if stage.no_progress_count >= policy.max_no_progress_rounds:
        return "no_progress"
    return None


def next_enabled_gate(policy: PlanClosedLoopPolicy, stages: dict[str, PlanStageState]) -> PlanGateId | None:
    for gate in canonical_gate_order():
        if gate not in enabled_gates(policy):
            continue
        stage = stages.get(f"plan_gate_{gate.value}")
        if stage is not None and stage.status is PlanStageStatus.PENDING:
            return gate
    return None


def build_disabled_outcome(detail: str = "plan closed-loop mode is off") -> PlanLoopOutcome:
    return PlanLoopOutcome(status="disabled", detail=detail)


def build_advisory_outcome(state: Any, policy: PlanClosedLoopPolicy) -> PlanLoopOutcome:
    active = next_enabled_gate(policy, state.plan_loop_stages)
    stage = state.plan_loop_stages.get(f"plan_gate_{active.value}") if active else None
    return PlanLoopOutcome(
        status="progressed", active_gate_id=active,
        active_stage_id=stage.stage_id if stage else None,
        additional_llm_calls_used=state.plan_loop_additional_llm_calls,
        state_changed=True,
        detail="foundation_only=true; reviewer and repair execution are not implemented",
    )
