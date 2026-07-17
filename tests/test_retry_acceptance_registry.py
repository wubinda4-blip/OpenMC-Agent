"""Phase 3B: owner acceptance registry actually runs checks."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.closed_loop.retry_acceptance import run_owner_acceptance_checks
from openmc_agent.plan_builder.closed_loop.retry_controller import compile_retry_execution_plan, normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_models import RetryTriggerOrigin
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state_with_universes(*, missing_required: bool = False) -> PlanBuildState:
    state = PlanBuildState(state_id="acceptance", requirement_text="reactor-neutral")
    cell = {"id": "c1", "role": "fuel", "material_id": "fuel"}
    universes = {"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [cell]}, {"universe_id": "abs", "kind": "custom", "cells": [dict(cell, id="c2")]}]}
    if missing_required:
        universes = {"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [cell]}]}
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content=universes, status="valid"))
    materials = {"patch_type": "materials", "materials": [{"material_id": "fuel", "density_g_cm3": 10.0, "role": "fuel"}]}
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content=materials, status="valid"))
    return state


def test_acceptance_runs_real_checks_and_records_executed_names() -> None:
    state = _state_with_universes()
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    policy = PlanClosedLoopPolicy(mode="controlled")
    plan = compile_retry_execution_plan(request, state, policy)
    result = run_owner_acceptance_checks(request=request, execution_plan=plan, clone_state=state, policy=policy)
    assert "universes_schema" in result.checks_executed
    assert "required_universe_ids" in result.checks_executed
    assert "universes_schema" in result.passed_checks


def test_acceptance_fails_when_required_universe_missing() -> None:
    state = _state_with_universes(missing_required=True)
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    policy = PlanClosedLoopPolicy(mode="controlled")
    plan = compile_retry_execution_plan(request, state, policy)
    result = run_owner_acceptance_checks(request=request, execution_plan=plan, clone_state=state, policy=policy)
    assert not result.accepted
    assert "required_universe_ids" in result.failed_checks
    assert any("abs" in issue.get("message", "") for issue in result.issues)


def test_acceptance_rejects_near_miss_ids() -> None:
    state = PlanBuildState(state_id="nearmiss", requirement_text="r")
    # Producer generated "absorber_v2" but the required ID is "absorber".
    universes = {"patch_type": "universes", "universes": [{"universe_id": "absorber_v2", "kind": "custom", "cells": [{"id": "c", "role": "absorber", "material_id": "m"}]}]}
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content=universes, status="valid"))
    materials = {"patch_type": "materials", "materials": [{"material_id": "m", "density_g_cm3": 1.0}]}
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content=materials, status="valid"))
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["absorber"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    policy = PlanClosedLoopPolicy(mode="controlled")
    plan = compile_retry_execution_plan(request, state, policy)
    result = run_owner_acceptance_checks(request=request, execution_plan=plan, clone_state=state, policy=policy)
    assert not result.accepted
    assert any("near_miss" in issue.get("code", "") or "missing" in issue.get("code", "") for issue in result.issues)
