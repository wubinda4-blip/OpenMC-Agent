"""Phase 8A Step 6C — gate review assignment coverage tests (Section 25: 44-46)."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.gate_review_assignment import (
    GATE_REVIEW_ASSIGNMENT_SCHEMA_VERSION,
    GateReviewAssignment,
    GateReviewAssignmentCoverage,
)


def test_assignment_has_stable_hash_and_id() -> None:
    a = GateReviewAssignment(
        gate_id="placement",
        call_id="call_1",
        assigned_requirement_ids=("req_1", "req_2"),
    )
    assert a.assignment_hash
    assert a.assignment_id.startswith("assignment_")


def test_assignment_hash_changes_on_different_inputs() -> None:
    a1 = GateReviewAssignment(
        gate_id="placement", assigned_requirement_ids=("req_1",),
    )
    a2 = GateReviewAssignment(
        gate_id="placement", assigned_requirement_ids=("req_2",),
    )
    assert a1.assignment_hash != a2.assignment_hash


def test_schema_version() -> None:
    assert GATE_REVIEW_ASSIGNMENT_SCHEMA_VERSION == "1.0"


# ---------------------------------------------------------------------------
# Coverage computation
# ---------------------------------------------------------------------------


def test_coverage_accumulates_from_successful_calls() -> None:
    """Successful calls contribute their assigned IDs to coverage."""

    cov = GateReviewAssignmentCoverage(gate_id="placement")
    a1 = GateReviewAssignment(
        gate_id="placement", call_id="c1",
        assigned_requirement_ids=("req_1", "req_2"),
        assigned_source_claim_ids=("sc_1",),
    )
    a2 = GateReviewAssignment(
        gate_id="placement", call_id="c2",
        assigned_requirement_ids=("req_3",),
        assigned_source_claim_ids=("sc_2",),
    )
    cov.add_call_outcome(
        assignment=a1, call_id="c1", success=True,
        reviewed_requirement_ids=("req_1",),  # reviewer saw at least req_1
    )
    cov.add_call_outcome(
        assignment=a2, call_id="c2", success=True,
        reviewed_requirement_ids=("req_3",),
    )
    # Coverage includes both assigned IDs and reviewer-reported IDs.
    assert set(cov.reviewed_requirement_ids) == {"req_1", "req_2", "req_3"}
    assert set(cov.reviewed_source_claim_ids) == {"sc_1", "sc_2"}


def test_failed_call_does_not_contribute_coverage() -> None:
    """A failed / truncated reviewer call does NOT count as covered."""

    cov = GateReviewAssignmentCoverage(gate_id="placement")
    a1 = GateReviewAssignment(
        gate_id="placement", call_id="c1",
        assigned_requirement_ids=("req_1", "req_2"),
    )
    cov.add_call_outcome(
        assignment=a1, call_id="c1", success=False,
        failure_code="review_truncated",
        reviewed_requirement_ids=("req_1",),  # partial — but call failed
    )
    assert cov.reviewed_requirement_ids == ()
    assert not cov.coverage_complete


def test_coverage_complete_when_expected_subset_covered() -> None:
    cov = GateReviewAssignmentCoverage(gate_id="placement")
    a1 = GateReviewAssignment(
        gate_id="placement", assigned_requirement_ids=("req_1", "req_2"),
    )
    cov.add_call_outcome(assignment=a1, call_id="c1", success=True)
    cov.recompute_coverage(
        expected_requirement_ids=("req_1", "req_2"),
        expected_source_claim_ids=(),
    )
    assert cov.coverage_complete is True


def test_coverage_incomplete_when_missing_expected() -> None:
    cov = GateReviewAssignmentCoverage(gate_id="placement")
    a1 = GateReviewAssignment(
        gate_id="placement", assigned_requirement_ids=("req_1",),
    )
    cov.add_call_outcome(assignment=a1, call_id="c1", success=True)
    cov.recompute_coverage(
        expected_requirement_ids=("req_1", "req_2"),  # req_2 missing
        expected_source_claim_ids=(),
    )
    assert cov.coverage_complete is False


def test_reviewer_self_report_does_not_override_failure() -> None:
    """Even if the reviewer claims it reviewed IDs, a failed call
    does not contribute coverage (P0: review_status='complete' trust)."""

    cov = GateReviewAssignmentCoverage(gate_id="placement")
    a1 = GateReviewAssignment(
        gate_id="placement", call_id="c1",
        assigned_requirement_ids=("req_1", "req_2"),
    )
    cov.add_call_outcome(
        assignment=a1, call_id="c1", success=False,
        failure_code="invalid_output",
        reviewed_requirement_ids=("req_1", "req_2"),  # reviewer claims complete
    )
    assert cov.reviewed_requirement_ids == ()


def test_no_expectations_means_trivially_complete() -> None:
    cov = GateReviewAssignmentCoverage(gate_id="placement")
    cov.recompute_coverage(
        expected_requirement_ids=(),
        expected_source_claim_ids=(),
    )
    assert cov.coverage_complete is True


# ---------------------------------------------------------------------------
# Reactor-neutrality
# ---------------------------------------------------------------------------


def test_assignment_no_reactor_specific_branches() -> None:
    from pathlib import Path
    import openmc_agent.plan_investigation.gate_review_assignment as mod
    src = Path(mod.__file__).read_text()
    for forbidden in ("vera3", "vera4", "pwr", "bwr", "vver", "htgr", "sfr", "candu"):
        assert forbidden not in src.lower(), (
            f"reactor-specific term {forbidden!r} found"
        )
