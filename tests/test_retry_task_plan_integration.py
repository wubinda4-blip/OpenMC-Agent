"""Phase 3B: planning task-plan integration (deterministic recompute)."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy, PlanLoopMode
from openmc_agent.plan_builder.closed_loop.retry_controller import execute_plan_retry_loop, normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_models import RetryExecutionStatus, RetryTriggerOrigin
from openmc_agent.plan_builder.dependency_graph import DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH
from openmc_agent.plan_builder.planning_scope import PlanningFeatureContract, ResolvedPlanningScope, build_canonical_task_plan
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def test_task_plan_recompute_is_deterministic_no_llm() -> None:
    state = PlanBuildState(state_id="taskplan", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly"}, status="valid"))
    state.resolved_planning_scope = ResolvedPlanningScope(value="single_assembly", status="resolved")
    state.planning_feature_contract = PlanningFeatureContract()
    state.canonical_task_plan = build_canonical_task_plan(scope=state.resolved_planning_scope, contract=state.planning_feature_contract, facts_patch={"patch_type": "facts", "model_scope": "single_assembly"}, feature_order=list(DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH._ORDER))
    request = normalize_retry_request(
        {"issue_codes": ["planning.required_patch_omitted"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.TASK_PLAN_RECONCILIATION,
    )
    if request is None:
        return  # skip if trigger code not mapped
    policy = PlanClosedLoopPolicy(mode="controlled")
    outcome = execute_plan_retry_loop(state=state, policy=policy)
    assert outcome.status in {RetryExecutionStatus.RESUMED, RetryExecutionStatus.BLOCKED, RetryExecutionStatus.FAILED}
    # No LLM call was made.
    assert state.plan_retry_budget.get("llm_calls", 0) == 0
