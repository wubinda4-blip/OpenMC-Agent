from __future__ import annotations

import pytest
from pydantic import ValidationError

from openmc_agent.plan_builder.closed_loop.models import (
    HumanPlanQuestion, HumanQuestionOption, PlanClosedLoopPolicy, PlanFindingCategory,
    PlanFindingSeverity, PlanGateId, PlanReviewAction, PlanReviewDecision,
    PlanReviewFinding, SourceExcerpt,
)
from openmc_agent.plan_builder.state import PlanBuildState


def _finding(**updates):
    payload = dict(gate_id="facts", code="facts.missing", severity="error", category="source_coverage", message="missing", confidence=0.5)
    payload.update(updates)
    return PlanReviewFinding(**payload)


def test_models_round_trip_and_source_hash() -> None:
    finding = _finding(source_evidence=[SourceExcerpt(source_id="s", source_path="a.md", line_start=1, line_end=2, text="x")])
    assert PlanReviewFinding.model_validate(finding.model_dump(mode="json")) == finding
    assert PlanBuildState.model_validate({"state_id": "old", "requirement_text": "r"}).plan_loop_stages == {}


def test_invalid_model_values_fail() -> None:
    with pytest.raises(ValidationError):
        _finding(confidence=1.1)
    with pytest.raises(ValidationError):
        PlanClosedLoopPolicy(max_repair_rounds_per_gate=-1)
    with pytest.raises(ValidationError):
        HumanPlanQuestion(question_id="q", gate_id="facts", title="t", question="?", options=[HumanQuestionOption(option_id="x", label="x", value=1, consequence=""), HumanQuestionOption(option_id="x", label="y", value=2, consequence="")])


def test_question_and_decision_protocol_rejects_invalid_semantics() -> None:
    with pytest.raises(ValidationError):
        HumanPlanQuestion(question_id="q", gate_id="facts", title="t", question="?", default_option_id="missing")
    with pytest.raises(ValidationError):
        HumanPlanQuestion(question_id="q", gate_id="facts", title="t", question="?", current_plan_summary='{"patch_type": "facts"}')
    with pytest.raises(ValidationError):
        PlanReviewDecision(decision_id="d", gate_id="facts", action="approve", target_patch_types=["facts"], allowed_actions_snapshot=["approve"], decided_by="deterministic")
    with pytest.raises(ValidationError):
        PlanReviewDecision(decision_id="d", gate_id="facts", action="retry_dependency", allowed_actions_snapshot=["retry_dependency"], decided_by="deterministic")
    with pytest.raises(ValidationError):
        PlanReviewDecision(decision_id="d", gate_id="facts", action="approve", allowed_actions_snapshot=["fail_closed"], decided_by="deterministic")
