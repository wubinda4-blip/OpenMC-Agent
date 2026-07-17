import json

from openmc_agent.plan_builder import executor
from openmc_agent.plan_builder.executor import run_incremental_planning
from openmc_agent.plan_builder.patch_generator import FakePatchLLM
from openmc_agent.plan_builder.state import PlanBuildState


def test_controlled_facts_approve_precedes_downstream(monkeypatch) -> None:
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
    result = run_incremental_planning(requirement="small source", state=PlanBuildState(state_id="s", requirement_text="small source"), llm_client=llm, plan_loop_policy={"mode": "controlled"}, plan_reviewer_client=reviewer)
    assert result.ok and captured["called"]
    assert result.state.plan_loop_stages["plan_gate_facts"].status.value == "accepted"
