from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy, PlanLoopMode
from openmc_agent.plan_builder.closed_loop.retry_controller import execute_plan_retry_loop, normalize_retry_request
from openmc_agent.plan_builder.state import PlanBuildState


def test_advisory_records_execution_plan_without_mutating_patches() -> None:
    state = PlanBuildState(state_id="retry-advisory", requirement_text="x")
    request = normalize_retry_request({"code": "facts.localized_insert_contract_missing"}, state=state)
    assert request is not None
    before = state.model_dump(mode="json")
    outcome = execute_plan_retry_loop(state=state, policy=PlanClosedLoopPolicy(mode=PlanLoopMode.ADVISORY))
    assert outcome.status.value == "retry_plan_recorded"
    assert state.patches == {}
    assert state.plan_retry_execution_plans
    assert before["plan_retry_requests"] == state.model_dump(mode="json")["plan_retry_requests"]
