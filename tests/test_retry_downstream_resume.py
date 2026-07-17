"""Phase 3B: downstream resume (non-recursive, depth-guarded)."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.downstream_resume import resume_incremental_from_patch, DownstreamResumeResult
from openmc_agent.plan_builder.state import PlanBuildState


def test_resume_returns_ok_when_no_invalidated_patches() -> None:
    state = PlanBuildState(state_id="resume", requirement_text="r")
    result = resume_incremental_from_patch(state=state, earliest_patch_type=None)
    assert result.ok


def test_resume_returns_failed_when_no_runner_provided() -> None:
    state = PlanBuildState(state_id="resume", requirement_text="r")
    result = resume_incremental_from_patch(state=state, earliest_patch_type="universes", run_incremental_fn=None)
    assert not result.ok
    assert result.failure_location == "no_runner"


def test_resume_depth_guard_prevents_unbounded_recursion() -> None:
    state = PlanBuildState(state_id="resume", requirement_text="r")
    state.metadata["phase3_retry_resume_depth"] = 10
    result = resume_incremental_from_patch(state=state, earliest_patch_type="universes", max_depth=6, run_incremental_fn=lambda **kwargs: None)
    assert not result.ok
    assert result.failure_location == "depth_guard"


def test_resume_calls_runner_and_reports_regenerated() -> None:
    state = PlanBuildState(state_id="resume", requirement_text="r")
    state.add_patch(__import__("openmc_agent.plan_builder.state", fromlist=["PlanPatchEnvelope"]).PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts"}, status="valid"))

    class _FakeResult:
        ok = True
        issues = []

    def _runner(**kwargs: object) -> _FakeResult:
        return _FakeResult()

    result = resume_incremental_from_patch(state=state, earliest_patch_type="universes", run_incremental_fn=_runner)
    assert result.ok
    assert result.earliest_resume_patch_type == "universes"
