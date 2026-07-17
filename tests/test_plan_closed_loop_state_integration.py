from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.controller import initialize_plan_loop_state, record_candidate
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def test_closed_loop_ledgers_round_trip_without_touching_plan_content() -> None:
    state = PlanBuildState(state_id="state", requirement_text="r", confirmed_facts={"confirmed": True})
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"benchmark_id": "generic"}, status="valid"))
    policy = PlanClosedLoopPolicy(mode="advisory", gate_enabled={"facts": True})
    stages = initialize_plan_loop_state(state, policy, ["facts"])
    record_candidate(state, stages[0], "issue", "candidate")
    state.plan_loop_additional_llm_calls = 0
    restored = PlanBuildState.model_validate(state.model_dump(mode="json"))
    assert restored.confirmed_facts == {"confirmed": True}
    assert restored.patches["facts"].content == {"benchmark_id": "generic"}
    assert restored.plan_loop_stages[stages[0].stage_id].patch_types == ["facts"]
    assert restored.plan_loop_issue_attempts_by_fingerprint == {"issue": 1}
    assert restored.plan_loop_candidate_hashes_by_fingerprint == {"issue": ["candidate"]}
    assert restored.plan_loop_additional_llm_calls == 0
