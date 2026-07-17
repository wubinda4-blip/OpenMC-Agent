import json

from openmc_agent.plan_builder.closed_loop.facts_evidence import build_facts_evidence_packs
from openmc_agent.plan_builder.closed_loop.facts_reviewer import run_facts_review
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy, PlanStageStatus
from openmc_agent.plan_builder.state import PlanBuildState
from openmc_agent.plan_builder.closed_loop.facts_human import build_facts_human_question
from openmc_agent.plan_builder.closed_loop.models import PlanGateId, PlanReviewFinding, PlanStageState, SourceExcerpt
from openmc_agent.graph import _plan_generation_router, _resume_plan_closed_loop


def test_schema_retry_repeats_evidence_pack_and_recovers_trailing_json() -> None:
    policy = PlanClosedLoopPolicy()
    packs = build_facts_evidence_packs(
        requirement_text="source line\n", facts_patch={"patch_type": "facts"},
        confirmed_facts={}, planning_metadata={}, policy=policy,
    )
    evidence = packs[0].source_excerpts[0].evidence_hash
    prompts: list[str] = []

    def reviewer(prompt: str) -> str:
        prompts.append(prompt)
        if len(prompts) == 1:
            return "I will now provide JSON. {not json}"
        return "explanation ignored " + json.dumps({
            "review_status": "complete", "findings": [],
            "reviewed_evidence_hashes": [evidence], "coverage_summary": {},
        })

    state = PlanBuildState(state_id="state", requirement_text="source line")
    result = run_facts_review(evidence_packs=packs, reviewer_client=reviewer, state=state, policy=policy)
    assert result.ok and result.coverage_complete
    assert result.schema_retries == 1 and state.plan_loop_additional_llm_calls == 2
    assert evidence in prompts[1] and "SCHEMA:" in prompts[1]


def test_review_call_budget_prevents_extra_call() -> None:
    policy = PlanClosedLoopPolicy(max_total_additional_llm_calls=0)
    packs = build_facts_evidence_packs(
        requirement_text="source", facts_patch={"patch_type": "facts"},
        confirmed_facts={}, planning_metadata={}, policy=policy,
    )
    result = run_facts_review(
        evidence_packs=packs, reviewer_client=lambda _: (_ for _ in ()).throw(AssertionError("must not call")),
        state=PlanBuildState(state_id="state", requirement_text="source"), policy=policy,
    )
    assert not result.ok and result.failure_code == "facts_review.budget_exhausted"


def test_review_failed_is_distinct_terminal_status() -> None:
    assert PlanStageStatus.REVIEW_FAILED.value == "review_failed"


def test_graph_resume_persists_typed_facts_answer() -> None:
    finding = PlanReviewFinding(
        gate_id="facts", code="facts.anchor_ambiguous", severity="error",
        category="physical_ambiguity", message="Choose an anchor.",
        source_evidence=[SourceExcerpt(source_id="source", text="anchor is unspecified")],
        affected_patch_types=["facts"], affected_json_paths=["/anchor"],
        requires_human=True, confidence=0.8,
        metadata={"candidate_interpretations": [
            {"option_id": "top", "label": "Top", "value": "top", "consequence": "top", "source_evidence_hashes": []},
            {"option_id": "bottom", "label": "Bottom", "value": "bottom", "consequence": "bottom", "source_evidence_hashes": []},
        ]},
    )
    question = build_facts_human_question(finding)
    state = PlanBuildState(state_id="state", requirement_text="source")
    state.plan_human_questions[question.question_id] = question
    state.plan_loop_stages["plan_gate_facts"] = PlanStageState(
        stage_id="plan_gate_facts", gate_id=PlanGateId.FACTS, status="awaiting_human",
    )
    serialized = state.model_dump(mode="json")
    assert _plan_generation_router({"plan_build_state": serialized}) == "ask_plan_expert"
    update = _resume_plan_closed_loop({
        "plan_build_state": serialized,
        "plan_human_answers": {question.question_id: {"selected_option_id": "top", "answered_by": "test"}},
    })
    resumed = PlanBuildState.model_validate(update["plan_build_state"])
    assert resumed.plan_human_answers[question.question_id].selected_option_id == "top"
