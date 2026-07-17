"""Typed ambiguity-only facts confirmation helpers."""

from __future__ import annotations

from .models import ConfirmedFactRecord, HumanPlanAnswer, HumanPlanQuestion, HumanQuestionOption, PlanReviewFinding


def build_facts_human_question(finding: PlanReviewFinding) -> HumanPlanQuestion:
    raw = finding.metadata.get("candidate_interpretations", [])
    options = [HumanQuestionOption.model_validate({key: item.get(key) for key in ("option_id", "label", "value", "consequence", "recommended") if key in item}) for item in raw if isinstance(item, dict)]
    if len(options) < 2:
        options.extend([
            HumanQuestionOption(option_id="retain_unconfirmed", label="Retain unconfirmed", value=None, consequence="Keep this fact unresolved.", recommended=False),
            HumanQuestionOption(option_id="custom", label="Custom value", value=None, consequence="Provide a source-backed custom value.", recommended=False),
        ])
    return HumanPlanQuestion(
        question_id=f"facts_question_{finding.finding_id[:16]}", gate_id="facts", finding_ids=[finding.finding_id],
        title="Facts source ambiguity", question=finding.message,
        source_evidence=finding.source_evidence, current_plan_summary="FactsPatch requires a confirmed value at: " + ", ".join(finding.affected_json_paths),
        options=options, affected_patch_types=["facts"], affected_json_paths=finding.affected_json_paths,
    )


def consume_facts_answer(*, question: HumanPlanQuestion, answer: HumanPlanAnswer, round_index: int) -> ConfirmedFactRecord:
    option_ids = {option.option_id: option for option in question.options}
    if answer.selected_option_id is not None and answer.selected_option_id not in option_ids:
        raise ValueError("selected option does not belong to question")
    value = answer.custom_value if answer.selected_option_id == "custom" else option_ids.get(answer.selected_option_id).value if answer.selected_option_id else answer.custom_value
    if not question.affected_json_paths:
        raise ValueError("human question has no facts path")
    return ConfirmedFactRecord(
        fact_id=f"confirmed_{question.question_id}", json_path=question.affected_json_paths[0], value=value,
        question_id=question.question_id, evidence_hashes=[item.evidence_hash for item in question.source_evidence],
        confirmed_round=round_index,
    )
