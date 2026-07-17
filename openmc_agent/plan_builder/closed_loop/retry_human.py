"""Typed human questions for retry requests that require human confirmation.

When a retry request has ``requires_human=True``, the controller creates a
typed :class:`HumanPlanQuestion` bound to the request fingerprint and gate
input hash.  The graph routes through ``ask_plan_expert`` and, on answer,
through ``resume_plan_retry`` which supersedes the old request and creates a
fresh one from the confirmed record.
"""

from __future__ import annotations

from typing import Any

from .models import HumanPlanAnswer, HumanPlanQuestion, HumanQuestionOption
from .retry_models import ExecutablePlanRetryRequest


def build_retry_human_question(
    request: ExecutablePlanRetryRequest,
    *,
    message: str = "",
    candidate_interpretations: list[dict[str, Any]] | None = None,
) -> HumanPlanQuestion:
    """Build a typed human question bound to a retry request.

    The question id and metadata encode the retry request id and fingerprint
    so the resume node can supersede the correct request and avoid consuming
    a stale answer bound to a different input hash.
    """
    options = [HumanQuestionOption.model_validate(item) for item in (candidate_interpretations or []) if isinstance(item, dict)]
    if len(options) < 2:
        options.extend([
            HumanQuestionOption(option_id="retain_unconfirmed", label="Retain unconfirmed", value=None, consequence="Leave this retry unresolved; the loop will block."),
            HumanQuestionOption(option_id="custom", label="Custom value", value=None, consequence="Supply a source-backed value for the owner producer."),
        ])
    gate_id = request.gate_id.value if request.gate_id else "placement"
    return HumanPlanQuestion(
        question_id=f"retry_question_{request.request_id}",
        gate_id=gate_id,
        finding_ids=list(request.source_finding_ids),
        title=f"Retry request requires confirmation: {request.reason_code}",
        question=message or f"The retry request {request.request_id} (reason={request.reason_code}) requires a human decision before the owner producer can continue.",
        current_plan_summary=f"Retry request {request.request_id} for owner(s) {', '.join(request.owner_patch_types)}",
        options=options,
        affected_patch_types=list(request.owner_patch_types),
        affected_json_paths=sorted({path for target in request.targets for path in target.affected_json_paths}),
        default_option_id="retain_unconfirmed",
        metadata={
            "retry_request_id": request.request_id,
            "retry_request_fingerprint": request.request_fingerprint,
            "input_hash": request.gate_input_hash or "",
            "reason_code": request.reason_code,
        },
    )


def validate_retry_human_answer(
    *,
    request: ExecutablePlanRetryRequest,
    question: HumanPlanQuestion,
    answer: HumanPlanAnswer,
) -> bool:
    """Return True if the answer is still valid for the active request.

    The answer is invalid if the request fingerprint or gate input hash has
    changed since the question was asked (e.g. because a prior retry commit
    invalidated it).
    """
    if question.question_id != answer.question_id:
        return False
    expected_fingerprint = question.metadata.get("retry_request_fingerprint", "")
    expected_hash = question.metadata.get("input_hash", "")
    if expected_fingerprint and expected_fingerprint != request.request_fingerprint:
        return False
    if expected_hash and request.gate_input_hash and expected_hash != request.gate_input_hash:
        return False
    return True


__all__ = ["build_retry_human_question", "validate_retry_human_answer"]
