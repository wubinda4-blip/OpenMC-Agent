"""Phase 8A Step 6C — assignment-based gate review coverage (Section 25).

The previous coverage check trusted the reviewer's self-reported
``review_status="complete"``.  This module adds an explicit
:class:`GateReviewAssignment` that the gate framework fills BEFORE
calling the reviewer, recording which requirement IDs / contract row
IDs / source claim IDs / object IDs the reviewer was asked to cover.

Coverage is computed from the assignment + the actual call outcome:

* Successful, schema-valid call → its assigned IDs count as reviewed.
* Failed / truncated / insufficient-evidence call → assigned IDs do
  NOT count as reviewed.

The reviewer's ``reviewed_requirement_ids`` / ``reviewed_source_claim_ids``
are recorded as audit cross-check, not as the only coverage basis.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from openmc_agent.schemas import AgentBaseModel

from .hashing import content_hash, short_id

__all__ = [
    "GateReviewAssignment",
    "GateReviewAssignmentCoverage",
    "GATE_REVIEW_ASSIGNMENT_SCHEMA_VERSION",
]


GATE_REVIEW_ASSIGNMENT_SCHEMA_VERSION = "1.0"


class GateReviewAssignment(AgentBaseModel):
    """One reviewer-call assignment for one gate.

    Created BEFORE the reviewer is invoked.  The gate framework shards
    the work (requirement IDs, contract rows, source claims, object
    IDs) into one or more :class:`GateReviewAssignment` instances so
    each call has a bounded, verifiable scope.
    """

    assignment_id: str = ""
    gate_id: str
    call_id: str = ""
    assigned_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    assigned_contract_row_ids: tuple[str, ...] = Field(default_factory=tuple)
    assigned_source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    assigned_object_ids: tuple[str, ...] = Field(default_factory=tuple)
    input_hash: str = ""
    assignment_hash: str = ""

    @model_validator(mode="after")
    def _compute_hash(self) -> "GateReviewAssignment":
        body = {
            "gate_id": self.gate_id,
            "call_id": self.call_id,
            "requirement_ids": list(self.assigned_requirement_ids),
            "contract_row_ids": list(self.assigned_contract_row_ids),
            "source_claim_ids": list(self.assigned_source_claim_ids),
            "object_ids": list(self.assigned_object_ids),
            "input_hash": self.input_hash,
        }
        h = content_hash(body)
        object.__setattr__(self, "assignment_hash", h)
        if not self.assignment_id:
            object.__setattr__(self, "assignment_id", short_id("assignment", h))
        return self


class GateReviewAssignmentCoverage(AgentBaseModel):
    """Aggregate coverage across all assignments for one gate.

    The gate framework records one :class:`GateReviewAssignment` per
    reviewer call and one entry in ``call_outcomes`` per call.  The
    ``covered_*`` sets are computed from the assignments whose
    corresponding call outcome was successful.
    """

    gate_id: str
    assignments: tuple[GateReviewAssignment, ...] = Field(default_factory=tuple)
    call_outcomes: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    reviewed_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    reviewed_source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    coverage_complete: bool = False

    def add_call_outcome(
        self,
        *,
        assignment: GateReviewAssignment,
        call_id: str,
        success: bool,
        reviewed_requirement_ids: tuple[str, ...] = (),
        reviewed_source_claim_ids: tuple[str, ...] = (),
        failure_code: str = "",
    ) -> None:
        """Record the outcome of one reviewer call.

        Mutates ``call_outcomes`` and the covered sets in place.  The
        caller must persist the updated :class:`GateReviewAssignmentCoverage`
        on the state after each call.
        """

        outcomes = list(self.call_outcomes)
        outcomes.append({
            "call_id": call_id,
            "assignment_id": assignment.assignment_id,
            "success": bool(success),
            "failure_code": failure_code,
            "reviewed_requirement_ids": list(reviewed_requirement_ids),
            "reviewed_source_claim_ids": list(reviewed_source_claim_ids),
        })
        object.__setattr__(self, "call_outcomes", tuple(outcomes))
        # Coverage accumulates from successful calls only.
        if success:
            covered_req = set(self.reviewed_requirement_ids)
            for rid in assignment.assigned_requirement_ids:
                covered_req.add(rid)
            for rid in reviewed_requirement_ids:
                covered_req.add(rid)
            covered_sc = set(self.reviewed_source_claim_ids)
            for sid in assignment.assigned_source_claim_ids:
                covered_sc.add(sid)
            for sid in reviewed_source_claim_ids:
                covered_sc.add(sid)
            object.__setattr__(self, "reviewed_requirement_ids", tuple(sorted(covered_req)))
            object.__setattr__(self, "reviewed_source_claim_ids", tuple(sorted(covered_sc)))

    def recompute_coverage(self, *, expected_requirement_ids, expected_source_claim_ids) -> None:
        """Recompute ``coverage_complete`` against the expected sets."""

        expected_req = set(expected_requirement_ids or ())
        expected_sc = set(expected_source_claim_ids or ())
        covered_req = set(self.reviewed_requirement_ids)
        covered_sc = set(self.reviewed_source_claim_ids)
        # If there are no expectations, coverage is trivially complete.
        req_complete = expected_req.issubset(covered_req) if expected_req else True
        sc_complete = expected_sc.issubset(covered_sc) if expected_sc else True
        object.__setattr__(self, "coverage_complete", bool(req_complete and sc_complete))
