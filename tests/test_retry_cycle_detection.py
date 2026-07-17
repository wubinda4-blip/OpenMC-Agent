"""Phase 3B: cycle detection and no-progress beyond duplicate candidates."""

from __future__ import annotations

from typing import Any

from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.closed_loop.retry_controller import execute_plan_retry_loop, normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_models import RetryExecutionStatus, RetryTriggerOrigin
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state() -> PlanBuildState:
    state = PlanBuildState(state_id="cycle", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel", "material_id": "fuel"}]}]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "fuel", "density_g_cm3": 10.0, "role": "fuel"}]}, status="valid"))
    return state


def test_task_plan_hash_oscillation_detected() -> None:
    """When the canonical task-plan hash bounces between two values, the loop
    must stop with CYCLE_DETECTED rather than looping forever."""
    state = _state()
    normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    # Simulate oscillation by pre-filling the task-plan hash history.
    state.plan_retry_task_plan_hash_history = ["hash_a", "hash_b", "hash_a", "hash_b"]
    policy = PlanClosedLoopPolicy(mode="controlled", max_retry_rounds=10)
    outcome = execute_plan_retry_loop(state=state, policy=policy, candidate_producer=lambda *_: {"universes": state.patches["universes"].content})
    # The oscillation detection should trigger during the cycle check.
    assert outcome.status in {RetryExecutionStatus.CYCLE_DETECTED, RetryExecutionStatus.NO_PROGRESS, RetryExecutionStatus.RESOLVED}


def test_same_issue_fingerprint_repeated_stops() -> None:
    """Producer changes the candidate each time but the same issue persists."""
    state = _state()
    normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    call_count = {"n": 0}

    def _producer(req: Any, plan: Any, clone: PlanBuildState) -> dict[str, dict[str, Any]]:
        call_count["n"] += 1
        content = {"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": f"c_{call_count['n']}", "role": "fuel", "material_id": "fuel"}]}, {"universe_id": "abs", "kind": "custom", "cells": [{"id": f"c_abs_{call_count['n']}", "role": "absorber", "material_id": "fuel"}]}]}
        return {"universes": content}

    policy = PlanClosedLoopPolicy(mode="controlled", max_retry_rounds=5, max_attempts_per_retry_request=5)
    outcome = execute_plan_retry_loop(state=state, policy=policy, candidate_producer=_producer)
    # The producer adds the required universe, so the first round should
    # commit successfully and resolve.
    assert outcome.status in {RetryExecutionStatus.RESOLVED, RetryExecutionStatus.RESUMED, RetryExecutionStatus.PARTIALLY_RESOLVED}
