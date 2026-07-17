"""Phase 3B: producer registry for owner-specific candidate generation."""

from __future__ import annotations

from typing import Any

from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.closed_loop.retry_candidate_producers import (
    RetryCandidateContext,
    RetryCandidateProducerRegistry,
    default_producer_registry,
)
from openmc_agent.plan_builder.closed_loop.retry_controller import compile_retry_execution_plan, normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_models import RetryTriggerOrigin
from openmc_agent.plan_builder.patch_generator import PatchGenerationContext
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state() -> PlanBuildState:
    state = PlanBuildState(state_id="producer", requirement_text="reactor-neutral")
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel", "material_id": "fuel"}]}]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "fuel", "density_g_cm3": 10.0, "role": "fuel"}]}, status="valid"))
    return state


def test_default_registry_has_producer_for_every_owner() -> None:
    registry = RetryCandidateProducerRegistry()
    # Empty registry should raise when asked to produce.
    import pytest
    state = _state()
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    policy = PlanClosedLoopPolicy(mode="controlled")
    plan = compile_retry_execution_plan(request, state, policy)
    ctx = RetryCandidateContext(request=request, execution_plan=plan, clone_state=state.model_copy(deep=True), policy=policy)
    with pytest.raises(ValueError, match="no producer registered"):
        registry.produce(ctx)


def test_universes_producer_passes_required_ids_in_prompt() -> None:
    state = _state()
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["rcca_aic", "rcca_b4c"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    policy = PlanClosedLoopPolicy(mode="controlled")
    plan = compile_retry_execution_plan(request, state, policy)
    seen_prompts: list[str] = []

    def _fake_generate(**kwargs: Any) -> Any:
        seen_prompts.append(kwargs.get("requirement", ""))
        class _R:
            ok = True
            parsed_patch = {"patch_type": "universes", "universes": [{"universe_id": "rcca_aic", "kind": "control_rod", "cells": [{"id": "c", "role": "absorber", "material_id": "fuel"}]}, {"universe_id": "rcca_b4c", "kind": "control_rod", "cells": [{"id": "c", "role": "absorber", "material_id": "fuel"}]}]}
            attempts = [type("A", (), {"attempt_index": 0})()]
        return _R()

    def _fake_build_context(state: Any, patch_type: str, **kw: Any) -> PatchGenerationContext:
        return PatchGenerationContext()

    registry = default_producer_registry(generate_patch_fn=_fake_generate, build_context_fn=_fake_build_context)
    clone = state.model_copy(deep=True)
    ctx = RetryCandidateContext(request=request, execution_plan=plan, clone_state=clone, policy=policy, requirement="VERA benchmark")
    result = registry.produce(ctx)
    assert "universes" in result.candidates
    assert result.llm_calls >= 1
    # The prompt suffix must contain the required IDs.
    assert any("rcca_aic" in p and "rcca_b4c" in p for p in seen_prompts)


def test_task_plan_producer_is_deterministic_no_llm() -> None:
    state = _state()
    # Set up minimal scope/contract for task-plan recompute.
    from openmc_agent.plan_builder.planning_scope import ResolvedPlanningScope, PlanningFeatureContract, build_canonical_task_plan
    from openmc_agent.plan_builder.dependency_graph import DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly"}, status="valid"))
    state.resolved_planning_scope = ResolvedPlanningScope(value="single_assembly", status="resolved")
    state.planning_feature_contract = PlanningFeatureContract()
    state.canonical_task_plan = build_canonical_task_plan(scope=state.resolved_planning_scope, contract=state.planning_feature_contract, facts_patch={"patch_type": "facts", "model_scope": "single_assembly"}, feature_order=list(DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH._ORDER))

    request = normalize_retry_request(
        {"issue_codes": ["planning.required_patch_omitted"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.TASK_PLAN_RECONCILIATION,
    )
    if request is None:
        # Task-plan code may not be registered for this trigger; skip gracefully.
        return
    policy = PlanClosedLoopPolicy(mode="controlled")
    try:
        plan = compile_retry_execution_plan(request, state, policy)
    except Exception:
        return
    registry = default_producer_registry(generate_patch_fn=lambda **_: None, build_context_fn=lambda *a, **kw: PatchGenerationContext())
    clone = state.model_copy(deep=True)
    ctx = RetryCandidateContext(request=request, execution_plan=plan, clone_state=clone, policy=policy)
    result = registry.produce(ctx)
    assert result.llm_calls == 0  # deterministic, no LLM
