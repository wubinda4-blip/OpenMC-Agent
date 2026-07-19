"""Source-validation + ledger binding for component evidence proposals.

Verifies that every :class:`ComponentEvidenceProposal`:

* References real SourceSpans in the supplied SourceIndex.
* Has a ``value`` whose numerical tokens appear verbatim in one of the
  referenced span excerpts (or is a deterministic derivation thereof).
* Does not contradict a human-confirmed claim.
* Does not collide with an accepted Facts field at the same semantic
  key with a different value.

Accepted proposals become :class:`EvidenceClaim` records in the shared
:class:`PlanningEvidenceLedger` with status ``explicit``.  Rejected
proposals become ``unresolved`` claims so the caller can see what
failed and why.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .component_evidence import (
    SUPPORTED_UNITS,
    UnitConversion,
    ComponentEvidenceProposal,
    ComponentEvidenceSynthesisResult,
    normalize_unit,
)
from .errors import PlanInvestigationIssue
from .evidence_ledger import (
    PlanningEvidenceLedger,
    add_claim,
)
from .hashing import content_hash
from .models import (
    EvidenceClaim,
    EvidenceCriticality,
    EvidenceSourceRef,
    EvidenceStatus,
)
from .source_index import SourceIndex

__all__ = [
    "ProposalValidationOutcome",
    "ProposalValidationReport",
    "validate_component_evidence_proposal",
    "accept_component_evidence_proposal",
    "bind_synthesis_result_to_ledger",
    "SOURCE_BACKED_PREDICATES",
]


# Predicates that REQUIRE source-backed evidence (cannot stand on
# derivation alone).  Others (geometry.profile_required, etc.) may be
# accepted based on Inventory context.
SOURCE_BACKED_PREDICATES: frozenset[str] = frozenset(
    {
        "geometry.profile_radius_boundary",
        "geometry.axial_region_extent",
        "material.density_present",
        "material.composition_present",
        "material.identity_present",
        "material.temperature_present",
    }
)


# ---------------------------------------------------------------------------
# Validation outcome
# ---------------------------------------------------------------------------


@dataclass
class ProposalValidationOutcome:
    """Per-proposal validation result.

    ``accepted`` proposals can be added to the ledger.  ``rejected``
    proposals carry a reason code; ``unresolved_value`` carries the
    field name that needs follow-up (e.g. radius not in source).
    """

    proposal_id: str
    accepted: bool
    reason_code: str = ""
    reason_message: str = ""
    unresolved_fields: tuple[str, ...] = field(default_factory=tuple)
    accepted_claim_id: str = ""


@dataclass
class ProposalValidationReport:
    """Aggregate report for one synthesis result."""

    patch_type: str
    outcomes: list[ProposalValidationOutcome] = field(default_factory=list)
    accepted_claim_ids: list[str] = field(default_factory=list)
    rejected_proposal_ids: list[str] = field(default_factory=list)
    unresolved_proposal_ids: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_type": self.patch_type,
            "accepted_count": len(self.accepted_claim_ids),
            "rejected_count": len(self.rejected_proposal_ids),
            "unresolved_count": len(self.unresolved_proposal_ids),
            "conflict_count": len(self.conflicts),
            "outcomes": [
                {
                    "proposal_id": o.proposal_id,
                    "accepted": o.accepted,
                    "reason_code": o.reason_code,
                    "unresolved_fields": list(o.unresolved_fields),
                }
                for o in self.outcomes
            ],
        }


# ---------------------------------------------------------------------------
# Per-proposal validation
# ---------------------------------------------------------------------------


def validate_component_evidence_proposal(
    proposal: ComponentEvidenceProposal,
    *,
    source_indexes: Mapping[str, SourceIndex],
    ledger: PlanningEvidenceLedger,
) -> ProposalValidationOutcome:
    """Validate one proposal against the supplied SourceIndex + Ledger.

    Returns a :class:`ProposalValidationOutcome`.  The caller decides
    whether to ``accept`` or surface as unresolved.
    """

    # 1. Every span_id must resolve in some source index.
    resolved_refs: list[EvidenceSourceRef] = []
    for span_id in proposal.source_span_ids:
        ref = _resolve_span_id(span_id, source_indexes)
        if ref is None:
            return ProposalValidationOutcome(
                proposal_id=proposal.proposal_id,
                accepted=False,
                reason_code="plan_investigation.source_span_unknown",
                reason_message=f"span_id {span_id} is not present in any source index",
            )
        resolved_refs.append(ref)

    # 2. Source-backed predicate requires at least one span.
    if proposal.predicate in SOURCE_BACKED_PREDICATES and not resolved_refs:
        return ProposalValidationOutcome(
            proposal_id=proposal.proposal_id,
            accepted=False,
            reason_code="plan_investigation.source_backed_predicate_missing_span",
            reason_message=(
                f"predicate {proposal.predicate} requires at least one source_span_id"
            ),
        )

    # 3. Numerical values must be source-token-recoverable (when the
    #    predicate is value-bearing).
    if proposal.predicate in SOURCE_BACKED_PREDICATES and proposal.value is not None:
        token_check = _value_tokens_present_in_spans(
            proposal.value, proposal.source_span_ids, source_indexes
        )
        if not token_check.present:
            return ProposalValidationOutcome(
                proposal_id=proposal.proposal_id,
                accepted=False,
                reason_code="plan_investigation.value_not_source_backed",
                reason_message=(
                    f"value {proposal.value!r} not recoverable from referenced spans "
                    f"(missing tokens: {token_check.missing})"
                ),
                unresolved_fields=(proposal.predicate,),
            )

    # 4. Semantic-key conflict against existing accepted claims.  We use
    #    (subject, predicate) as the semantic key so proposals compete
    #    with existing claims at the same logical address.
    semantic_key = _semantic_key_for_proposal(proposal)
    for existing in ledger.claims.values():
        if _semantic_key_for_claim(existing) != semantic_key:
            continue
        if existing.value != proposal.value:
            return ProposalValidationOutcome(
                proposal_id=proposal.proposal_id,
                accepted=False,
                reason_code="plan_investigation.evidence_conflict",
                reason_message=(
                    f"proposal conflicts with existing claim {existing.claim_id}"
                ),
            )

    return ProposalValidationOutcome(
        proposal_id=proposal.proposal_id,
        accepted=True,
    )


def accept_component_evidence_proposal(
    proposal: ComponentEvidenceProposal,
    *,
    source_indexes: Mapping[str, SourceIndex],
    ledger: PlanningEvidenceLedger,
    patch_type: str,
) -> tuple[str, EvidenceClaim]:
    """Accept a validated proposal and add it to the ledger.

    Returns ``(claim_id, claim)``.  Raises if the proposal fails
    validation; the caller is expected to call
    :func:`validate_component_evidence_proposal` first.
    """

    outcome = validate_component_evidence_proposal(
        proposal, source_indexes=source_indexes, ledger=ledger
    )
    if not outcome.accepted:
        raise PlanInvestigationIssue(
            outcome.reason_code or "plan_investigation.proposal_invalid",
            outcome.reason_message,
        )
    # Build source_refs from the resolved spans.
    source_refs: list[EvidenceSourceRef] = []
    for span_id in proposal.source_span_ids:
        ref = _resolve_span_id(span_id, source_indexes)
        if ref is not None:
            source_refs.append(ref)
    criticality = (
        EvidenceCriticality.SOURCE_CRITICAL
        if proposal.predicate in SOURCE_BACKED_PREDICATES
        else EvidenceCriticality.SUPPORTING
    )
    claim = EvidenceClaim(
        claim_id="",
        subject=proposal.subject,
        predicate=proposal.predicate,
        value=proposal.value,
        status=EvidenceStatus.EXPLICIT,
        criticality=criticality,
        source_refs=tuple(source_refs),
        required_by_patch_types=(patch_type,),
        metadata={
            "component_kind": proposal.component_kind,
            "profile_kind": proposal.profile_kind,
            "applicability": proposal.applicability,
            "proposal_id": proposal.proposal_id,
            "cell_roles": list(proposal.cell_roles),
            "material_roles": list(proposal.material_roles),
        },
    )
    add_claim(ledger, claim, source_indexes=dict(source_indexes))
    return claim.claim_id, claim


# ---------------------------------------------------------------------------
# Bulk synthesis-result binder
# ---------------------------------------------------------------------------


def bind_synthesis_result_to_ledger(
    *,
    result: ComponentEvidenceSynthesisResult,
    source_indexes: Mapping[str, SourceIndex],
    ledger: PlanningEvidenceLedger,
) -> ProposalValidationReport:
    """Validate + accept every proposal in ``result``.

    Rejected proposals are recorded in the report; the caller decides
    whether to block (controlled) or warn (advisory).
    """

    report = ProposalValidationReport(patch_type=result.patch_type)
    for proposal in result.proposals:
        outcome = validate_component_evidence_proposal(
            proposal, source_indexes=source_indexes, ledger=ledger
        )
        report.outcomes.append(outcome)
        if outcome.accepted:
            try:
                claim_id, _ = accept_component_evidence_proposal(
                    proposal,
                    source_indexes=source_indexes,
                    ledger=ledger,
                    patch_type=result.patch_type,
                )
                outcome.accepted_claim_id = claim_id
                report.accepted_claim_ids.append(claim_id)
            except PlanInvestigationIssue as issue:
                outcome.accepted = False
                outcome.reason_code = issue.code
                outcome.reason_message = issue.message
                report.rejected_proposal_ids.append(proposal.proposal_id)
        else:
            if outcome.unresolved_fields:
                report.unresolved_proposal_ids.append(proposal.proposal_id)
            else:
                report.rejected_proposal_ids.append(proposal.proposal_id)
    return report


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_span_id(
    span_id: str,
    source_indexes: Mapping[str, SourceIndex],
) -> EvidenceSourceRef | None:
    """Find a registered span across all source indexes."""

    for source_id, idx in source_indexes.items():
        span = idx._registered_spans.get(span_id)
        if span is not None:
            return EvidenceSourceRef(
                source_id=source_id,
                span_id=span_id,
                excerpt_hash=span.excerpt_hash,
            )
    return None


@dataclass
class _TokenCheck:
    present: bool
    missing: list[str] = field(default_factory=list)


def _value_tokens_present_in_spans(
    value: Any,
    span_ids: Iterable[str],
    source_indexes: Mapping[str, SourceIndex],
) -> _TokenCheck:
    """Verify that every numerical token in ``value`` is recoverable from
    the referenced span excerpts.

    Strings / bools / None are accepted as-is (they don't carry
    numerical claims).  Numbers must appear in the excerpt (with unit
    suffix optional).  Lists / dicts are recursed into.
    """

    tokens = _collect_numerical_tokens(value)
    if not tokens:
        return _TokenCheck(present=True)
    excerpts = _collect_excerpts(span_ids, source_indexes)
    if not excerpts:
        return _TokenCheck(present=False, missing=tokens)
    missing: list[str] = []
    for token in tokens:
        if not _token_in_any_excerpt(token, excerpts):
            missing.append(token)
    return _TokenCheck(present=not missing, missing=missing)


def _collect_numerical_tokens(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        out.append(_format_number(value))
        return out
    if isinstance(value, str):
        # Extract numbers from the string.
        for match in re.finditer(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", value):
            out.append(match.group(0))
        return out
    if isinstance(value, (list, tuple)):
        for item in value:
            out.extend(_collect_numerical_tokens(item))
        return out
    if isinstance(value, dict):
        for v in value.values():
            out.extend(_collect_numerical_tokens(v))
        return out
    return out


def _format_number(value: float | int) -> str:
    """Render a number the way it might appear in a source document.

    The check is intentionally permissive: 3.5 might appear as 3.5 or
    3.50 in the source, but the canonical short form is checked first.
    """

    if isinstance(value, int):
        return str(value)
    # Trim trailing zeros for floats.
    rendered = f"{value:.10g}"
    return rendered


def _collect_excerpts(
    span_ids: Iterable[str],
    source_indexes: Mapping[str, SourceIndex],
) -> list[str]:
    excerpts: list[str] = []
    for span_id in span_ids:
        for idx in source_indexes.values():
            span = idx._registered_spans.get(span_id)
            if span is not None:
                excerpts.append(span.excerpt)
                break
    return excerpts


def _token_in_any_excerpt(token: str, excerpts: Iterable[str]) -> bool:
    for excerpt in excerpts:
        if token in excerpt:
            return True
        # Try without leading "0" for the integer part (e.g. 0.5 vs .5).
        if token.startswith("0.") and token[2:] in excerpt:
            return True
    return False


def _semantic_key_for_proposal(proposal: ComponentEvidenceProposal) -> str:
    """Match the ledger-level semantic key (subject + predicate)."""

    return content_hash({"s": proposal.subject, "p": proposal.predicate})


def _semantic_key_for_claim(claim: EvidenceClaim) -> str:
    return content_hash({"s": claim.subject, "p": claim.predicate})
