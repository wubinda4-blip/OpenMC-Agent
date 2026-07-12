"""Tests for expert feedback decisions + acknowledgements (P0-D5A Sections 5,6,7)."""
from __future__ import annotations

from openmc_agent.expert_feedback import (
    ExpertFeedbackDecision,
    build_assumption_acknowledgements,
    interpret_empty_feedback,
)


def test_accept_does_not_modify_composition_status() -> None:
    """accept_assumptions_for_this_run only records an acknowledgement; it never
    flips composition_status to confirmed."""
    decision = ExpertFeedbackDecision(
        action="accept_assumptions_for_this_run",
        acknowledged_question_ids=["material_composition:fuel"],
        reason="user accepted approximations for this run",
    )
    acks = build_assumption_acknowledgements(
        decision,
        question_ids=["material_composition:fuel"],
        round_index=1,
        plan_hash="abc123",
    )
    assert len(acks) == 1
    ack = acks[0]
    assert ack.status == "accepted_for_run"
    # No field on the acknowledgement carries a composition_status mutation;
    # the acknowledgement only records the user's disposition + plan hash.
    assert ack.question_id == "material_composition:fuel"
    assert ack.answer is None
    dumped = ack.model_dump()
    assert "composition_status" not in dumped
    assert "confirmed" not in str(dumped).lower().replace("acknowledgement", "")


def test_accept_does_not_modify_material_composition() -> None:
    """Acknowledgements carry no nuclide fraction / density / boron ppm fields."""
    decision = ExpertFeedbackDecision(
        action="accept_assumptions_for_this_run",
        acknowledged_question_ids=["m1", "m2"],
    )
    acks = build_assumption_acknowledgements(
        decision, question_ids=["m1", "m2"], round_index=1, plan_hash="h"
    )
    for ack in acks:
        payload = ack.model_dump()
        # The acknowledgement is a run-level record only.
        assert set(payload.keys()) <= {
            "question_id",
            "status",
            "answer",
            "round_index",
            "plan_hash",
            "timestamp",
        }


def test_accept_creates_acknowledgement() -> None:
    decision = ExpertFeedbackDecision(
        action="accept_assumptions_for_this_run",
        acknowledged_question_ids=["m1"],
    )
    acks = build_assumption_acknowledgements(
        decision, question_ids=["m1"], round_index=2, plan_hash="h1"
    )
    assert len(acks) == 1
    assert acks[0].status == "accepted_for_run"
    assert acks[0].round_index == 2
    assert acks[0].plan_hash == "h1"


def test_defer_keeps_pending_and_records_deferred() -> None:
    decision = ExpertFeedbackDecision(action="defer_confirmations")
    acks = build_assumption_acknowledgements(
        decision, question_ids=["m1", "m2"], round_index=1, plan_hash="h"
    )
    assert len(acks) == 2
    assert all(a.status == "deferred" for a in acks)


def test_empty_feedback_runnable_defers() -> None:
    """Empty enter on a runnable model = defer (explicit), not vague continue."""
    decision = interpret_empty_feedback(renderability="runnable", has_blocking_issue=False)
    assert decision.action == "defer_confirmations"
    assert "defer" in decision.reason.lower() or "continue" in decision.reason.lower()


def test_empty_feedback_skeleton_accepts_review_only() -> None:
    """Empty enter on a skeleton = accept_review_only (BLOCKED_REVIEW_ONLY)."""
    decision = interpret_empty_feedback(renderability="skeleton", has_blocking_issue=True)
    assert decision.action == "accept_review_only"
    assert "review" in decision.reason.lower()


def test_plan_hash_change_prevents_blind_ack_reuse() -> None:
    """Acknowledgements are scoped to a plan hash; a different hash is a
    different acknowledgement and must not be blindly reused."""
    d = ExpertFeedbackDecision(action="accept_assumptions_for_this_run", acknowledged_question_ids=["m1"])
    a1 = build_assumption_acknowledgements(d, question_ids=["m1"], round_index=1, plan_hash="hash_a")
    a2 = build_assumption_acknowledgements(d, question_ids=["m1"], round_index=1, plan_hash="hash_b")
    assert a1[0].plan_hash == "hash_a"
    assert a2[0].plan_hash == "hash_b"
    assert a1[0].plan_hash != a2[0].plan_hash


def test_resolved_question_not_re_acked_dedup() -> None:
    """Duplicate question_ids within one decision produce one acknowledgement."""
    d = ExpertFeedbackDecision(
        action="accept_assumptions_for_this_run",
        acknowledged_question_ids=["m1", "m1", "m2"],
    )
    acks = build_assumption_acknowledgements(
        d, question_ids=["m1", "m2"], round_index=1, plan_hash="h"
    )
    ids = [a.question_id for a in acks]
    assert ids == ["m1", "m2"]


def test_continue_repair_and_abort_decisions_valid() -> None:
    for action in ("continue_repair", "accept_review_only", "abort", "provide_corrections"):
        d = ExpertFeedbackDecision(action=action)  # type: ignore[arg-type]
        assert d.action == action
