"""Typed ambiguity-only Placement Gate questions and confirmation records."""

from __future__ import annotations

from .models import ConfirmedPlanFactRecord, HumanPlanAnswer, HumanPlanQuestion, HumanQuestionOption, PlanReviewFinding


def build_placement_human_question(finding: PlanReviewFinding, *, input_hash: str) -> HumanPlanQuestion:
    raw = finding.metadata.get("candidate_interpretations", [])
    options = [HumanQuestionOption.model_validate(item) for item in raw if isinstance(item, dict)]
    if len(options) < 2:
        options.extend([
            HumanQuestionOption(option_id="retain_unconfirmed", label="Retain unconfirmed", value=None, consequence="Leave this placement interpretation unresolved."),
            HumanQuestionOption(option_id="custom", label="Custom value", value=None, consequence="Supply a source-backed placement value."),
        ])
    return HumanPlanQuestion(
        question_id=f"placement_question_{finding.finding_id[:16]}", gate_id="placement", finding_ids=[finding.finding_id],
        title="Placement contract ambiguity", question=finding.message, current_plan_summary="Placement contract requires confirmation at: " + ", ".join(finding.affected_json_paths),
        options=options, affected_patch_types=list(finding.affected_patch_types), affected_json_paths=list(finding.affected_json_paths),
        metadata={"evidence_refs": finding.metadata.get("evidence_refs", []), "input_hash": input_hash},
    )


def consume_placement_answer(*, question: HumanPlanQuestion, answer: HumanPlanAnswer, round_index: int) -> ConfirmedPlanFactRecord:
    options = {option.option_id: option for option in question.options}
    if answer.selected_option_id is not None and answer.selected_option_id not in options:
        raise ValueError("selected option does not belong to question")
    value = answer.custom_value if answer.selected_option_id == "custom" else options.get(answer.selected_option_id).value if answer.selected_option_id else answer.custom_value
    if not question.affected_patch_types or not question.affected_json_paths:
        raise ValueError("placement human question has no scoped target")
    return ConfirmedPlanFactRecord(
        fact_id=f"confirmed_{question.question_id}", gate_id="placement", patch_type=question.affected_patch_types[0],
        json_path=question.affected_json_paths[0], value=value, question_id=question.question_id,
        evidence_refs=list(question.metadata.get("evidence_refs", [])), affected_patch_types=list(question.affected_patch_types),
        confirmed_round=round_index, input_hash=str(question.metadata.get("input_hash", "")),
    )
