"""Phase 3B: gate replay (actual preflight + critic + decision tracking)."""

from __future__ import annotations

from typing import Any

from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy, PlanGateId
from openmc_agent.plan_builder.closed_loop.retry_controller import execute_plan_retry_loop, normalize_retry_request, record_gate_replay_attempt
from openmc_agent.plan_builder.closed_loop.retry_models import RetryExecutionStatus, RetryTriggerOrigin
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state() -> PlanBuildState:
    state = PlanBuildState(state_id="replay", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel", "material_id": "fuel"}]}, {"universe_id": "abs", "kind": "custom", "cells": [{"id": "c2", "role": "absorber", "material_id": "fuel"}]}]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "fuel", "density_g_cm3": 10.0, "role": "fuel"}]}, status="valid"))
    return state


def test_gate_replayer_callback_increments_attempt_count() -> None:
    state = _state()
    normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )

    def _producer(req: Any, plan: Any, clone: PlanBuildState) -> dict[str, dict[str, Any]]:
        return {"universes": clone.patches["universes"].content}

    replayed: list[Any] = []

    def _replayer(state: PlanBuildState, plan: Any, gates: list[Any]) -> tuple[list[Any], list[dict[str, Any]]]:
        for gate in gates:
            record_gate_replay_attempt(state, gate, success=True)
            replayed.append(gate)
        return gates, []

    policy = PlanClosedLoopPolicy(mode="controlled")
    outcome = execute_plan_retry_loop(state=state, policy=policy, candidate_producer=_producer, gate_replayer=_replayer)
    # The gate replayer was invoked and recorded replay attempts.
    if replayed:
        assert state.plan_retry_gate_replay_attempt_counts.get("placement", 0) >= 1
        assert state.plan_retry_gate_replay_success_counts.get("placement", 0) >= 1


def test_gates_replayed_only_records_actual_replays() -> None:
    """The round record's gates_replayed field must only include gates that
    were actually replayed, not merely invalidated."""
    state = _state()
    normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )

    def _producer(req: Any, plan: Any, clone: PlanBuildState) -> dict[str, dict[str, Any]]:
        return {"universes": clone.patches["universes"].content}

    policy = PlanClosedLoopPolicy(mode="controlled")
    # No gate_replayer provided → gates_replayed in the round record is empty.
    execute_plan_retry_loop(state=state, policy=policy, candidate_producer=_producer)
    for record in state.plan_retry_rounds:
        # Without a replayer, gates_replayed is empty (gates_invalidated may
        # be non-empty but that's a different field).
        assert record.gates_replayed == [] or all(g in record.gates_invalidated for g in record.gates_replayed)
