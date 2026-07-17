from __future__ import annotations

import pytest

from openmc_agent.plan_builder.closed_loop.controller import InvalidPlanLoopTransition, record_no_progress, transition_stage
from openmc_agent.plan_builder.closed_loop.models import PlanStageState, PlanStageStatus
from openmc_agent.plan_builder.state import PlanBuildState


def test_allowed_and_illegal_transitions_include_context() -> None:
    stage = PlanStageState(stage_id="s", gate_id="facts")
    transition_stage(stage, PlanStageStatus.PROPOSING)
    transition_stage(stage, PlanStageStatus.VALIDATING)
    transition_stage(stage, PlanStageStatus.REVIEWING)
    transition_stage(stage, PlanStageStatus.ACCEPTED)
    with pytest.raises(InvalidPlanLoopTransition, match=r"stage_id=s.*from=accepted.*to=repairing"):
        transition_stage(stage, PlanStageStatus.REPAIRING)
    reviewing = PlanStageState(stage_id="r", gate_id="facts", status="reviewing")
    transition_stage(reviewing, PlanStageStatus.REPAIRING)
    transition_stage(reviewing, PlanStageStatus.VALIDATING)
    human = PlanStageState(stage_id="h", gate_id="facts", status="awaiting_human")
    transition_stage(human, PlanStageStatus.REPAIRING)
    blocked = PlanStageState(stage_id="b", gate_id="facts", status="blocked")
    with pytest.raises(InvalidPlanLoopTransition):
        transition_stage(blocked, PlanStageStatus.REVIEWING)
    with pytest.raises(InvalidPlanLoopTransition):
        transition_stage(human, PlanStageStatus.ACCEPTED)


def test_no_progress_is_persisted_only_for_duplicate_candidate() -> None:
    state = PlanBuildState(state_id="s", requirement_text="r")
    stage = PlanStageState(stage_id="stage", gate_id="facts")
    assert record_no_progress(state, stage, "issue", "candidate") is False
    assert record_no_progress(state, stage, "issue", "candidate") is True
    restored = PlanBuildState.model_validate(state.model_dump(mode="json"))
    assert restored.plan_loop_issue_attempts_by_fingerprint == {"issue": 2}
    assert restored.plan_loop_candidate_hashes_by_fingerprint == {"issue": ["candidate", "candidate"]}
