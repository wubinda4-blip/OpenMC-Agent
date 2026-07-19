"""Phase 8A Step 6B — typed research models (Sections 10-11).

Defines the request/result protocol for evidence-retrieval research
that runs INSIDE the closed-loop retry path:

* :class:`PlanResearchTarget` — one target predicate (e.g.
  ``geometry.axial_region_extent`` for ``upper_plenum``).
* :class:`PlanResearchRequest` — a typed request covering one or more
  gate findings; built deterministically from the gate's finding list.
* :class:`PlanResearchResult` — outcome of executing a research
  request against the existing SourceIndex + Ledger.
* :class:`PlanningEvidenceDelta` — diff between the ledger before and
  after the research (added claim ids, span ids, conflicts).
* :class:`SourceAbsenceRecord` — recorded when a target predicate
  could not be located in the source index after a bounded search.

These models are deliberately reactor-neutral: no field references a
specific reactor type, and no enum value is tied to PWR / BWR / VVER
/etc.  All reactor-specific data lives in the requirement text and
the source documents.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from openmc_agent.schemas import AgentBaseModel

from .constraint_payloads import CONSTRAINT_SCHEMA_VERSION
from .hashing import content_hash, short_id

__all__ = [
    "PlanResearchTarget",
    "PlanResearchRequest",
    "PlanResearchExecutionPlan",
    "PlanResearchResult",
    "PlanResearchStatus",
    "PlanningEvidenceDelta",
    "SourceAbsenceRecord",
    "RESEARCH_SCHEMA_VERSION",
]


RESEARCH_SCHEMA_VERSION = "1.0"


class PlanResearchStatus:
    """Allowed status values for :class:`PlanResearchResult`.

    Phase 8A Step 7 (Section 4) splits the previous ``evidence_added``
    into two distinct statuses:

    * ``candidate_spans_found`` — search located SourceSpans that MAY
      be relevant, but no typed semantic EvidenceClaim has been
      accepted into the Ledger yet.  The gate MUST NOT reopen on this
      status; no evidence has been committed.
    * ``evidence_added`` — at least one new typed semantic EvidenceClaim
      was accepted into the Ledger AND ``ledger_hash_after !=
      ledger_hash_before``.  Only this status authorises gate reopen.

    The previous minimal executor conflated "found spans" with
    "evidence committed", which could cause the gate to reopen
    without any actual Ledger change (Section 4 P0).
    """

    CANDIDATE_SPANS_FOUND = "candidate_spans_found"
    EVIDENCE_ADDED = "evidence_added"
    NO_EVIDENCE_FOUND = "no_evidence_found"
    CONFLICT_FOUND = "conflict_found"
    REQUIRES_HUMAN = "requires_human"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INVALID_OUTPUT = "invalid_output"
    NO_PROGRESS = "no_progress"
    BLOCKED = "blocked"


_ALLOWED_RESEARCH_STATUSES = {
    PlanResearchStatus.CANDIDATE_SPANS_FOUND,
    PlanResearchStatus.EVIDENCE_ADDED,
    PlanResearchStatus.NO_EVIDENCE_FOUND,
    PlanResearchStatus.CONFLICT_FOUND,
    PlanResearchStatus.REQUIRES_HUMAN,
    PlanResearchStatus.BUDGET_EXHAUSTED,
    PlanResearchStatus.INVALID_OUTPUT,
    PlanResearchStatus.NO_PROGRESS,
    PlanResearchStatus.BLOCKED,
}


# ---------------------------------------------------------------------------
# PlanResearchTarget
# ---------------------------------------------------------------------------


class PlanResearchTarget(AgentBaseModel):
    """One target predicate for evidence retrieval.

    Example::

        PlanResearchTarget(
            target_id="target_fuel_density",
            claim_predicates=("material.density",),
            target_json_paths=("/materials[*].density_g_per_cc",),
            target_component_ids=("fuel_pin",),
            target_profile_ids=("fuel_pin_v1",),
            target_requirement_ids=("mreq_001",),
            expected_value_kind="numeric:positive",
            preferred_source_sections=("materials", "composition"),
            suggested_search_terms=("fuel density", "UO2 density"),
            blocking_patch_types=("materials",),
            requires_human_if_absent=False,
        )
    """

    target_id: str = ""
    claim_predicates: tuple[str, ...] = Field(default_factory=tuple)
    target_json_paths: tuple[str, ...] = Field(default_factory=tuple)
    target_component_ids: tuple[str, ...] = Field(default_factory=tuple)
    target_profile_ids: tuple[str, ...] = Field(default_factory=tuple)
    target_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    expected_value_kind: str = ""
    preferred_source_sections: tuple[str, ...] = Field(default_factory=tuple)
    suggested_search_terms: tuple[str, ...] = Field(default_factory=tuple)
    blocking_patch_types: tuple[str, ...] = Field(default_factory=tuple)
    requires_human_if_absent: bool = False
    target_hash: str = ""

    @model_validator(mode="after")
    def _compute_hash(self) -> "PlanResearchTarget":
        body = {
            "predicates": list(self.claim_predicates),
            "json_paths": list(self.target_json_paths),
            "components": list(self.target_component_ids),
            "profiles": list(self.target_profile_ids),
            "requirements": list(self.target_requirement_ids),
            "expected_value_kind": self.expected_value_kind,
        }
        h = content_hash(body)
        object.__setattr__(self, "target_hash", h)
        if not self.target_id:
            object.__setattr__(self, "target_id", short_id("target", h))
        return self


# ---------------------------------------------------------------------------
# PlanResearchRequest
# ---------------------------------------------------------------------------


class PlanResearchRequest(AgentBaseModel):
    """A typed research request covering one or more gate findings."""

    request_id: str = ""
    gate_id: str
    finding_ids: tuple[str, ...] = Field(default_factory=tuple)
    finding_fingerprints: tuple[str, ...] = Field(default_factory=tuple)
    categories: tuple[str, ...] = Field(default_factory=tuple)
    issue_codes: tuple[str, ...] = Field(default_factory=tuple)
    targets: tuple[PlanResearchTarget, ...] = Field(default_factory=tuple)
    preferred_tools: tuple[str, ...] = Field(default_factory=tuple)
    owner_patch_types: tuple[str, ...] = Field(default_factory=tuple)
    downstream_patch_types: tuple[str, ...] = Field(default_factory=tuple)
    ledger_hash_before: str = ""
    inventory_hash_before: str = ""
    gate_input_hash: str = ""
    research_round: int = 1
    budget: dict[str, Any] = Field(default_factory=dict)
    request_fingerprint: str = ""

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> "PlanResearchRequest":
        body = {
            "gate_id": self.gate_id,
            "finding_ids": list(self.finding_ids),
            "issue_codes": list(self.issue_codes),
            "targets": [t.target_hash for t in self.targets],
            "ledger_hash_before": self.ledger_hash_before,
            "inventory_hash_before": self.inventory_hash_before,
            "gate_input_hash": self.gate_input_hash,
        }
        h = content_hash(body)
        object.__setattr__(self, "request_fingerprint", h)
        if not self.request_id:
            object.__setattr__(self, "request_id", short_id("research", h))
        return self


# ---------------------------------------------------------------------------
# PlanResearchExecutionPlan
# ---------------------------------------------------------------------------


class PlanResearchExecutionPlan(AgentBaseModel):
    """Deterministic plan for how to execute one research request."""

    request_id: str
    mandatory_actions: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    supplemental_actions_allowed: bool = False
    synthesis_required: bool = False
    owner_regeneration_required: bool = False
    inventory_recompile_required: bool = False
    downstream_invalidation_required: bool = False
    plan_hash: str = ""

    @model_validator(mode="after")
    def _compute_hash(self) -> "PlanResearchExecutionPlan":
        body = {
            "mandatory_actions": list(self.mandatory_actions),
            "supplemental_allowed": self.supplemental_actions_allowed,
            "synthesis_required": self.synthesis_required,
            "owner_regen": self.owner_regeneration_required,
            "inv_recompile": self.inventory_recompile_required,
            "downstream_inv": self.downstream_invalidation_required,
        }
        h = content_hash(body)
        object.__setattr__(self, "plan_hash", h)
        return self


# ---------------------------------------------------------------------------
# PlanningEvidenceDelta
# ---------------------------------------------------------------------------


class PlanningEvidenceDelta(AgentBaseModel):
    """Diff between the ledger before and after one research request.

    ``delta_hash`` is computed from the added ids; the auditor uses
    this to detect no-progress (same delta hash seen before).
    """

    delta_id: str = ""
    request_id: str
    ledger_hash_before: str
    ledger_hash_after: str
    added_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    added_source_span_ids: tuple[str, ...] = Field(default_factory=tuple)
    added_conflict_ids: tuple[str, ...] = Field(default_factory=tuple)
    resolved_unresolved_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    newly_unresolved_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    human_confirmation_ids: tuple[str, ...] = Field(default_factory=tuple)
    delta_hash: str = ""

    @model_validator(mode="after")
    def _compute_hash(self) -> "PlanningEvidenceDelta":
        body = {
            "added_claim_ids": list(self.added_claim_ids),
            "added_source_span_ids": list(self.added_source_span_ids),
            "added_conflict_ids": list(self.added_conflict_ids),
            "resolved": list(self.resolved_unresolved_claim_ids),
            "newly_unresolved": list(self.newly_unresolved_claim_ids),
            "human": list(self.human_confirmation_ids),
            "before": self.ledger_hash_before,
            "after": self.ledger_hash_after,
        }
        h = content_hash(body)
        object.__setattr__(self, "delta_hash", h)
        if not self.delta_id:
            object.__setattr__(self, "delta_id", short_id("delta", h))
        return self

    @property
    def is_empty(self) -> bool:
        """True when the delta added nothing."""

        return (
            not self.added_claim_ids
            and not self.added_source_span_ids
            and not self.added_conflict_ids
            and not self.resolved_unresolved_claim_ids
            and not self.human_confirmation_ids
        )


# ---------------------------------------------------------------------------
# SourceAbsenceRecord
# ---------------------------------------------------------------------------


class SourceAbsenceRecord(AgentBaseModel):
    """Recorded when a target could not be located after a bounded search.

    Source absence only means: "the current SourceIndex did not yield a
    SourceSpan for this target predicate within the policy budget."  It
    is NOT a positive claim that the information does not exist.

    When ``requires_human_if_absent=True`` on the originating target,
    the router converts this into ``ASK_HUMAN``.
    """

    absence_id: str = ""
    request_id: str
    target_id: str
    source_ids_searched: tuple[str, ...] = Field(default_factory=tuple)
    query_fingerprints: tuple[str, ...] = Field(default_factory=tuple)
    section_filters: tuple[str, ...] = Field(default_factory=tuple)
    search_result_counts: dict[str, int] = Field(default_factory=dict)
    search_complete_within_policy: bool = False
    absence_hash: str = ""

    @model_validator(mode="after")
    def _compute_hash(self) -> "SourceAbsenceRecord":
        body = {
            "request_id": self.request_id,
            "target_id": self.target_id,
            "source_ids": list(self.source_ids_searched),
            "queries": list(self.query_fingerprints),
            "sections": list(self.section_filters),
            "result_counts": dict(self.search_result_counts),
            "complete": self.search_complete_within_policy,
        }
        h = content_hash(body)
        object.__setattr__(self, "absence_hash", h)
        if not self.absence_id:
            object.__setattr__(self, "absence_id", short_id("absence", h))
        return self


# ---------------------------------------------------------------------------
# PlanResearchResult
# ---------------------------------------------------------------------------


class PlanResearchResult(AgentBaseModel):
    """Outcome of executing a :class:`PlanResearchRequest`.

    The closed-loop controller inspects ``status``:

    * ``evidence_added`` → owner patch can be regenerated with new evidence.
    * ``no_evidence_found`` → check ``absence_records``; route to
      ``ASK_HUMAN`` when ``requires_human_if_absent`` was set.
    * ``conflict_found`` → ``ASK_HUMAN``.
    * ``requires_human`` → ``ASK_HUMAN``.
    * ``budget_exhausted`` → ``FAIL_CLOSED`` or ``ASK_HUMAN``.
    * ``no_progress`` → ``FAIL_CLOSED`` (same request fingerprint
      already ran without producing new evidence).
    * ``invalid_output`` / ``blocked`` → ``FAIL_CLOSED``.
    """

    request_id: str
    status: str
    tool_calls: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    evidence_delta: PlanningEvidenceDelta | None = None
    ledger_hash_after: str = ""
    inventory_hash_after: str = ""
    resolved_finding_ids: tuple[str, ...] = Field(default_factory=tuple)
    unresolved_finding_ids: tuple[str, ...] = Field(default_factory=tuple)
    requires_human_finding_ids: tuple[str, ...] = Field(default_factory=tuple)
    absence_records: tuple[SourceAbsenceRecord, ...] = Field(default_factory=tuple)
    no_progress: bool = False
    budget_used: dict[str, Any] = Field(default_factory=dict)
    result_hash: str = ""
    warnings: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _validate_status(self) -> "PlanResearchResult":
        if self.status not in _ALLOWED_RESEARCH_STATUSES:
            raise ValueError(
                f"unknown research status={self.status!r}; allowed: "
                f"{sorted(s for s in _ALLOWED_RESEARCH_STATUSES)}"
            )
        body = {
            "request_id": self.request_id,
            "status": self.status,
            "tool_calls": list(self.tool_calls),
            "delta_hash": (
                self.evidence_delta.delta_hash if self.evidence_delta else ""
            ),
            "resolved": list(self.resolved_finding_ids),
            "unresolved": list(self.unresolved_finding_ids),
            "human": list(self.requires_human_finding_ids),
            "no_progress": self.no_progress,
        }
        object.__setattr__(self, "result_hash", content_hash(body))
        return self
