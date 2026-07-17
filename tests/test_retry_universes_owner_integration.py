"""Phase 3B: Universes owner integration — exact required IDs, near-miss rejection."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.retry_acceptance import run_owner_acceptance_checks
from openmc_agent.plan_builder.closed_loop.retry_controller import compile_retry_execution_plan, normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_models import RetryTriggerOrigin
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state(universe_ids: list[str]) -> PlanBuildState:
    state = PlanBuildState(state_id="uni-int", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [{"universe_id": uid, "kind": "custom", "cells": [{"id": "c", "role": "x", "material_id": "fuel"}]} for uid in universe_ids]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "fuel", "density_g_cm3": 1.0}]}, status="valid"))
    return state


def test_universes_acceptance_preserves_unrelated_required_universe() -> None:
    """When the producer regenerates universes, unrelated required IDs must survive."""
    state = _state(["fuel", "abs", "pyrex_u"])
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    policy = PlanClosedLoopPolicy(mode="controlled")
    plan = compile_retry_execution_plan(request, state, policy)
    # Clone still has all three universes — acceptance must pass.
    result = run_owner_acceptance_checks(request=request, execution_plan=plan, clone_state=state, policy=policy)
    assert "required_universe_ids" in result.passed_checks


def test_near_miss_id_rejected() -> None:
    """Producer generates rcca_aic_v2 but the required ID is rcca_aic."""
    state = _state(["rcca_aic_v2"])
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["rcca_aic"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    policy = PlanClosedLoopPolicy(mode="controlled")
    plan = compile_retry_execution_plan(request, state, policy)
    result = run_owner_acceptance_checks(request=request, execution_plan=plan, clone_state=state, policy=policy)
    assert not result.accepted
    assert any("rcca_aic" in issue.get("message", "") for issue in result.issues)
