"""Phase 3B: gate invalidation vs replay count separation."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy, PlanGateId, PlanStageStatus
from openmc_agent.plan_builder.closed_loop.controller import initialize_gate_stage
from openmc_agent.plan_builder.closed_loop.retry_controller import invalidate_gates_for_patch_change, record_gate_replay_attempt
from openmc_agent.plan_builder.state import PlanBuildState


def test_invalidation_increments_invalidation_count_not_replay_attempt() -> None:
    state = PlanBuildState(state_id="gate-sep", requirement_text="r")
    stage = initialize_gate_stage(PlanGateId.PLACEMENT, [])
    state.plan_loop_stages[stage.stage_id] = stage
    invalidated = invalidate_gates_for_patch_change(state, ["assembly_catalog"])
    assert PlanGateId.PLACEMENT in invalidated
    assert state.plan_retry_gate_invalidation_counts.get("placement") == 1
    # Replay attempt count is NOT incremented by invalidation alone.
    assert state.plan_retry_gate_replay_attempt_counts.get("placement", 0) == 0


def test_replay_attempt_only_incremented_when_actually_replayed() -> None:
    state = PlanBuildState(state_id="gate-sep", requirement_text="r")
    record_gate_replay_attempt(state, PlanGateId.FACTS, success=True)
    assert state.plan_retry_gate_replay_attempt_counts.get("facts") == 1
    assert state.plan_retry_gate_replay_success_counts.get("facts") == 1
    record_gate_replay_attempt(state, PlanGateId.FACTS, success=False)
    assert state.plan_retry_gate_replay_attempt_counts.get("facts") == 2
    assert state.plan_retry_gate_replay_success_counts.get("facts") == 1


def test_invalidation_preserves_prior_accepted_hash() -> None:
    state = PlanBuildState(state_id="gate-sep", requirement_text="r")
    stage = initialize_gate_stage(PlanGateId.FACTS, [])
    stage.metadata["accepted_input_hash"] = "old_hash_123"
    state.plan_loop_stages[stage.stage_id] = stage
    invalidate_gates_for_patch_change(state, ["facts"])
    assert stage.metadata.get("prior_accepted_input_hash") == "old_hash_123"
    assert stage.status is PlanStageStatus.PENDING
