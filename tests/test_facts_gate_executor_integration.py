import json

from openmc_agent.plan_builder import executor
from openmc_agent.plan_builder.closed_loop.controller import initialize_plan_loop_state, transition_stage
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy, PlanStageStatus
from openmc_agent.plan_builder.executor import run_incremental_planning
from openmc_agent.plan_builder.patch_generator import FakePatchLLM
from openmc_agent.plan_builder.patches import FactsPatch
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def test_controlled_facts_approve_precedes_downstream(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(executor, "default_patch_task_order", lambda _: ["facts"])
    monkeypatch.setattr(executor, "required_patch_types_for_state", lambda _: ["facts"])
    monkeypatch.setattr(executor, "assemble_state_if_ready", lambda state, **_: state.model_copy(update={"assembled_plan": {"ok": True}}))
    llm = FakePatchLLM([json.dumps({"patch_type": "facts"})])
    captured = {}
    def reviewer(_):
        captured["called"] = True
        # Evidence is only known after prompt construction; recover it from prompt payload.
        data = json.loads(_.split("INPUT:\n", 1)[1])
        evidence = data["source_excerpts"][0]["evidence_hash"]
        return json.dumps({"review_status": "complete", "reviewed_evidence_hashes": [evidence], "coverage_summary": {}, "findings": []})
    result = run_incremental_planning(requirement="small source", state=PlanBuildState(state_id="s", requirement_text="small source"), llm_client=llm, plan_loop_policy={"mode": "controlled"}, plan_reviewer_client=reviewer, plan_loop_output_dir=tmp_path)
    assert result.ok and captured["called"]
    assert result.state.plan_loop_stages["plan_gate_facts"].status.value == "accepted"
    gate_result = json.loads((tmp_path / "incremental" / "plan_closed_loop" / "facts_gate_result.json").read_text())
    assert gate_result["initial_decision"]["action"] == "approve"
    assert gate_result["candidate_validation"] == {"attempted": False, "final": None, "rounds": []}
    assert gate_result["candidate_commit"] == {"attempted": False, "committed": False}
    assert gate_result["final_gate_status"]["status"] == "accepted"


def test_controlled_resume_never_bypasses_blocked_facts_gate(monkeypatch) -> None:
    """A graph retry must not skip a valid facts envelope below a blocked gate."""
    monkeypatch.setattr(executor, "default_patch_task_order", lambda _: ["facts", "materials"])
    monkeypatch.setattr(executor, "required_patch_types_for_state", lambda _: ["facts", "materials"])

    state = PlanBuildState(state_id="blocked", requirement_text="source")
    state.add_patch(PlanPatchEnvelope(
        patch_id="facts_1",
        patch_type="facts",
        content=FactsPatch().model_dump(mode="json"),
        status="valid",
    ))
    policy = PlanClosedLoopPolicy(mode="controlled", gate_enabled={"facts": True})
    initialize_plan_loop_state(state, policy, ["facts", "materials"])
    transition_stage(state.plan_loop_stages["plan_gate_facts"], PlanStageStatus.BLOCKED)

    result = run_incremental_planning(
        requirement="source",
        state=state,
        llm_client=lambda _prompt: (_ for _ in ()).throw(AssertionError("downstream proposer must not run")),
        plan_loop_policy=policy,
    )

    assert not result.ok
    assert [issue.code for issue in result.issues] == ["planning.facts_gate_not_accepted"]
    assert result.plan_loop_outcome["active_gate_id"] == "facts"
    assert not any(env.patch_type == "materials" for env in state.patches.values())
