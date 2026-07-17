"""Phase 3B: budget enforcement (LLM calls, owner regenerations, gate replays)."""

from __future__ import annotations

from typing import Any

from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.closed_loop.retry_controller import execute_plan_retry_loop, normalize_retry_request, record_gate_replay_attempt, _budget_snapshot
from openmc_agent.plan_builder.closed_loop.retry_models import PlanGateId, RetryExecutionStatus, RetryTriggerOrigin
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state() -> PlanBuildState:
    state = PlanBuildState(state_id="budget", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel", "material_id": "fuel"}]}, {"universe_id": "abs", "kind": "custom", "cells": [{"id": "c2", "role": "absorber", "material_id": "fuel"}]}]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "fuel", "density_g_cm3": 10.0, "role": "fuel"}]}, status="valid"))
    return state


def test_llm_calls_incremented_after_producer_invocation() -> None:
    state = _state()
    normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert state.plan_retry_budget.get("llm_calls", 0) == 0
    policy = PlanClosedLoopPolicy(mode="controlled")

    def _producer(req: Any, plan: Any, clone: PlanBuildState) -> dict[str, dict[str, Any]]:
        return {"universes": clone.patches["universes"].content}

    execute_plan_retry_loop(state=state, policy=policy, candidate_producer=_producer)
    assert state.plan_retry_budget.get("llm_calls", 0) >= 1


def test_owner_regenerations_tracked_per_patch() -> None:
    state = _state()
    normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    policy = PlanClosedLoopPolicy(mode="controlled")

    def _producer(req: Any, plan: Any, clone: PlanBuildState) -> dict[str, dict[str, Any]]:
        return {"universes": clone.patches["universes"].content}

    execute_plan_retry_loop(state=state, policy=policy, candidate_producer=_producer)
    assert state.plan_retry_owner_regenerations.get("universes", 0) >= 1


def test_gate_replay_success_count_tracked() -> None:
    state = _state()
    record_gate_replay_attempt(state, PlanGateId.FACTS, success=True)
    record_gate_replay_attempt(state, PlanGateId.FACTS, success=True)
    record_gate_replay_attempt(state, PlanGateId.FACTS, success=False)
    assert state.plan_retry_gate_replay_attempt_counts["facts"] == 3
    assert state.plan_retry_gate_replay_success_counts["facts"] == 2


def test_budget_snapshot_includes_all_counters() -> None:
    state = _state()
    state.plan_retry_budget["llm_calls"] = 5
    state.plan_retry_owner_regenerations["universes"] = 2
    state.plan_retry_gate_invalidation_counts["placement"] = 1
    state.plan_retry_gate_replay_attempt_counts["placement"] = 1
    state.plan_retry_gate_replay_success_counts["placement"] = 1
    snapshot = _budget_snapshot(state)
    assert snapshot["llm_calls"] == 5
    assert snapshot["owner_regenerations"] == 2
    assert snapshot["gate_invalidation_total"] == 1
    assert snapshot["gate_replay_attempts"] == 1
    assert snapshot["gate_replay_successes"] == 1
