from openmc_agent.plan_builder.closed_loop.facts_human import build_facts_human_question, consume_facts_answer
from openmc_agent.plan_builder.closed_loop.models import HumanPlanAnswer, PlanReviewFinding, SourceExcerpt


def test_typed_human_answer_becomes_confirmed_fact() -> None:
    finding = PlanReviewFinding(
        gate_id="facts", code="facts.anchor_ambiguous", severity="error", category="physical_ambiguity",
        message="anchor ambiguous", source_evidence=[SourceExcerpt(source_id="s", text="anchor unspecified")],
        affected_patch_types=["facts"], affected_json_paths=["/localized_insert_requirements/0/anchor_z_cm"],
        requires_human=True, confidence=0.8,
        metadata={"candidate_interpretations": [
            {"option_id": "top", "label": "Top", "value": "top", "consequence": "top reference", "source_evidence_hashes": []},
            {"option_id": "bottom", "label": "Bottom", "value": "bottom", "consequence": "bottom reference", "source_evidence_hashes": []},
        ]},
    )
    question = build_facts_human_question(finding)
    record = consume_facts_answer(question=question, answer=HumanPlanAnswer(question_id=question.question_id, selected_option_id="top", answered_by="test"), round_index=1)
    assert record.value == "top" and record.source == "human_confirmation"
