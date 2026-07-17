"""Phase 3B: retry human gate (question creation, answer validation, resume)."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.models import HumanPlanAnswer
from openmc_agent.plan_builder.closed_loop.retry_controller import normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_human import build_retry_human_question, validate_retry_human_answer
from openmc_agent.plan_builder.closed_loop.retry_models import RetryTriggerOrigin
from openmc_agent.plan_builder.state import PlanBuildState


def test_human_question_carries_request_id_and_fingerprint() -> None:
    state = PlanBuildState(state_id="human", requirement_text="r")
    request = normalize_retry_request(
        {"issue_codes": ["facts.model_scope_conflicts_with_planning_features"], "requires_human": True, "reason": "ambiguous scope"},
        state=state, origin=RetryTriggerOrigin.FACTS_GATE,
    )
    assert request is not None
    question = build_retry_human_question(request, message="scope is ambiguous")
    assert question.metadata["retry_request_id"] == request.request_id
    assert question.metadata["retry_request_fingerprint"] == request.request_fingerprint
    assert question.question_id.startswith("retry_question_")


def test_human_answer_invalidated_by_fingerprint_change() -> None:
    state = PlanBuildState(state_id="human", requirement_text="r")
    request = normalize_retry_request(
        {"issue_codes": ["facts.model_scope_conflicts_with_planning_features"], "requires_human": True, "reason": "ambiguous", "gate_input_hash": "hash_v1"},
        state=state, origin=RetryTriggerOrigin.FACTS_GATE,
    )
    assert request is not None
    question = build_retry_human_question(request)
    answer = HumanPlanAnswer(question_id=question.question_id, answered_by="user")
    assert validate_retry_human_answer(request=request, question=question, answer=answer)
    # Simulate the request being superseded by one with a different fingerprint.
    request2 = request.model_copy(update={"request_fingerprint": "different_hash"})
    assert not validate_retry_human_answer(request=request2, question=question, answer=answer)
