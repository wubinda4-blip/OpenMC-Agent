from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.artifacts import PlanLoopArtifactWriter
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.state import PlanBuildState


def test_artifacts_are_json_and_under_incremental_subdirectory(tmp_path) -> None:
    writer = PlanLoopArtifactWriter(tmp_path)
    policy_path = writer.write_plan_loop_policy(PlanClosedLoopPolicy(mode="advisory"))
    state_path = writer.write_plan_loop_state(PlanBuildState(state_id="s", requirement_text="r"))
    summary_path = writer.write_plan_loop_summary({"mode": "advisory", "additional_llm_calls": 0})
    assert policy_path and state_path and summary_path
    assert (tmp_path / "incremental" / "plan_closed_loop" / "plan_loop_policy.json").exists()
    assert PlanClosedLoopPolicy.model_validate_json((tmp_path / "incremental" / "plan_closed_loop" / "plan_loop_policy.json").read_text()).mode.value == "advisory"
    assert '"additional_llm_calls": 0' in (tmp_path / "incremental" / "plan_closed_loop" / "plan_loop_summary.json").read_text()


def test_artifact_write_failure_is_best_effort(tmp_path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    assert PlanLoopArtifactWriter(blocked).write_plan_loop_policy(PlanClosedLoopPolicy()) is None
