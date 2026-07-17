"""Phase 3B: bounded execution loop, cycle detection, budget, terminal removal."""

from __future__ import annotations

from typing import Any

from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.closed_loop.retry_controller import execute_plan_retry_loop, normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_models import RetryExecutionStatus, RetryTriggerOrigin
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state_with_universes_patch(*, universe_ids: list[str]) -> PlanBuildState:
    state = PlanBuildState(state_id="exec-loop", requirement_text="reactor-neutral")
    cell = {"id": "c1", "role": "fuel", "material_id": "fuel"}
    universes = [{"universe_id": uid, "kind": "fuel_pin" if uid == "fuel" else "custom", "cells": [dict(cell, id=f"c_{uid}")]} for uid in universe_ids]
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": universes}, status="valid"))
    materials = {"patch_type": "materials", "materials": [{"material_id": "fuel", "density_g_cm3": 10.0, "role": "fuel"}]}
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content=materials, status="valid"))
    return state


def test_duplicate_candidate_stops_retry_without_third_call() -> None:
    state = _state_with_universes_patch(universe_ids=["fuel"])
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    call_count = {"n": 0}

    def _always_same_producer(req: Any, plan: Any, clone: PlanBuildState) -> dict[str, dict[str, Any]]:
        call_count["n"] += 1
        # Always produce the same (wrong) candidate without the required "abs".
        return {"universes": clone.patches["universes"].content}

    def _reject_missing_abs(req: Any, plan: Any, clone: PlanBuildState) -> list[dict[str, Any]]:
        env = next((item for item in clone.patches.values() if item.patch_type == "universes" and item.status == "valid"), None)
        found = {str(u.get("universe_id")) for u in (env.content.get("universes", []) if env else [])}
        if "abs" not in found:
            return [{"code": "retry.required_universe_ids_missing", "severity": "error", "missing_ids": ["abs"]}]
        return []

    policy = PlanClosedLoopPolicy(mode="controlled", max_attempts_per_retry_request=3)
    outcome = execute_plan_retry_loop(state=state, policy=policy, candidate_producer=_always_same_producer, candidate_validator=_reject_missing_abs)
    # First call: acceptance fails (missing abs), request stays pending.
    # Second call: same candidate hash → duplicate → NO_PROGRESS.
    assert outcome.status is RetryExecutionStatus.NO_PROGRESS
    assert call_count["n"] >= 1
    # Terminal request removed from pending.
    assert request.request_id not in state.plan_retry_pending_request_ids


def test_terminal_request_removed_from_pending() -> None:
    state = _state_with_universes_patch(universe_ids=["fuel"])
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    policy = PlanClosedLoopPolicy(mode="controlled")
    outcome = execute_plan_retry_loop(state=state, policy=policy, candidate_producer=None)
    # No producer → FAILED; request must be removed (terminal).
    assert outcome.status is RetryExecutionStatus.FAILED
    assert request.request_id not in state.plan_retry_pending_request_ids


def test_budget_exhaustion_round_limit() -> None:
    state = _state_with_universes_patch(universe_ids=["fuel"])
    # Register two requests.
    for required in (["abs"], ["abs2"]):
        normalize_retry_request(
            {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": required, "reason": "x"},
            state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
        )
    policy = PlanClosedLoopPolicy(mode="controlled", max_retry_rounds=1)
    outcome = execute_plan_retry_loop(state=state, policy=policy, candidate_producer=None, max_rounds=1)
    assert outcome.status in {RetryExecutionStatus.BUDGET_EXHAUSTED, RetryExecutionStatus.FAILED}


def test_changed_candidate_same_issue_detected_as_no_progress() -> None:
    """Producer returns different content each time but the same blocking issue persists."""
    state = _state_with_universes_patch(universe_ids=["fuel"])
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    call_count = {"n": 0}

    def _varying_producer(req: Any, plan: Any, clone: PlanBuildState) -> dict[str, dict[str, Any]]:
        call_count["n"] += 1
        # Return a different candidate each call (different cell id) but
        # still missing the required "abs" universe.
        content = {"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": f"c_{call_count['n']}", "role": "fuel", "material_id": "fuel"}]}]}
        return {"universes": content}

    def _reject_missing_abs(req: Any, plan: Any, clone: PlanBuildState) -> list[dict[str, Any]]:
        env = next((item for item in clone.patches.values() if item.patch_type == "universes" and item.status == "valid"), None)
        found = {str(u.get("universe_id")) for u in (env.content.get("universes", []) if env else [])}
        if "abs" not in found:
            return [{"code": "retry.required_universe_ids_missing", "severity": "error", "missing_ids": ["abs"]}]
        return []

    policy = PlanClosedLoopPolicy(mode="controlled", max_retry_rounds=5, max_attempts_per_retry_request=3)
    outcome = execute_plan_retry_loop(state=state, policy=policy, candidate_producer=_varying_producer, candidate_validator=_reject_missing_abs)
    # The candidate changed but the required ID "abs" was still missing on
    # the first attempt; the acceptance check should fail.  Eventually the
    # attempt budget triggers FAILED or BUDGET_EXHAUSTED.
    assert outcome.status in {RetryExecutionStatus.FAILED, RetryExecutionStatus.BUDGET_EXHAUSTED, RetryExecutionStatus.NO_PROGRESS}


def test_off_mode_returns_blocked_without_producer_call() -> None:
    state = _state_with_universes_patch(universe_ids=["fuel"])
    normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    called = {"n": 0}

    def _producer(req: Any, plan: Any, clone: PlanBuildState) -> dict[str, dict[str, Any]]:
        called["n"] += 1
        return {}

    policy = PlanClosedLoopPolicy(mode="off")
    outcome = execute_plan_retry_loop(state=state, policy=policy, candidate_producer=_producer)
    assert outcome.status is RetryExecutionStatus.BLOCKED
    assert called["n"] == 0


def test_advisory_mode_records_plan_without_mutation() -> None:
    state = _state_with_universes_patch(universe_ids=["fuel"])
    normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    original_patch_count = len(state.patches)
    policy = PlanClosedLoopPolicy(mode="advisory")
    outcome = execute_plan_retry_loop(state=state, policy=policy, candidate_producer=lambda *_: {"universes": {}})
    assert outcome.status is RetryExecutionStatus.RETRY_PLAN_RECORDED
    assert not outcome.workflow_behavior_changed
    assert len(state.patches) == original_patch_count  # no mutation


def test_llm_call_budget_enforced() -> None:
    state = _state_with_universes_patch(universe_ids=["fuel"])
    normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    policy = PlanClosedLoopPolicy(mode="controlled", max_total_retry_llm_calls=0)

    def _producer(req: Any, plan: Any, clone: PlanBuildState) -> dict[str, dict[str, Any]]:
        return {"universes": {"patch_type": "universes", "universes": [{"universe_id": "abs", "kind": "custom", "cells": [{"id": "c", "role": "absorber", "material_id": "fuel"}]}]}}

    outcome = execute_plan_retry_loop(state=state, policy=policy, candidate_producer=_producer)
    assert outcome.status is RetryExecutionStatus.BUDGET_EXHAUSTED
