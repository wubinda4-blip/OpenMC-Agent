"""Tests for evidence conflict detection and human-confirmed immutability."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.errors import PlanInvestigationIssue
from openmc_agent.plan_investigation.evidence_ledger import (
    add_claim,
    create_empty_ledger,
    detect_conflicts,
    upsert_claim,
)
from openmc_agent.plan_investigation.models import (
    ConflictResolutionStatus,
    EvidenceClaim,
    EvidenceSourceRef,
    EvidenceStatus,
    SourceKind,
)
from openmc_agent.plan_investigation.source_index import build_source_index


def _idx():
    idx = build_source_index(
        text="alpha\nbeta\n",
        title="t",
        source_kind=SourceKind.USER_REQUIREMENT,
    )
    span = idx.make_span(1, 1)
    idx.register_span(span)
    return idx, span


def _explicit(value, *, source_id, span_id, excerpt_hash, critical=None, confirmed=False, hc_id=None):
    from openmc_agent.plan_investigation.models import EvidenceCriticality
    return EvidenceClaim(
        claim_id="",
        subject="core",
        predicate="size",
        value=value,
        status=EvidenceStatus.EXPLICIT,
        criticality=critical or EvidenceCriticality.SUPPORTING,
        source_refs=(EvidenceSourceRef(source_id=source_id, span_id=span_id, excerpt_hash=excerpt_hash),),
        confirmed_by_human=confirmed,
        human_confirmation_id=hc_id,
    )


def test_conflict_emitted_for_distinct_values() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    c1 = _explicit(3, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    c2 = _explicit(5, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ld, c1, source_indexes={idx.document.source_id: idx})
    add_claim(ld, c2, source_indexes={idx.document.source_id: idx})
    detect_conflicts(ld)
    assert len(ld.conflicts) == 1
    conflict = next(iter(ld.conflicts.values()))
    assert conflict.resolution_status == ConflictResolutionStatus.UNRESOLVED
    assert set(conflict.claim_ids) == {c1.claim_id, c2.claim_id}
    assert len(conflict.conflicting_values) == 2


def test_no_conflict_when_values_agree() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    c1 = _explicit(3, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    # Same value but via a separate span.
    span2 = idx.make_span(2, 2)
    idx.register_span(span2)
    c2 = EvidenceClaim(
        claim_id="",
        subject="core",
        predicate="size",
        value=3,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(EvidenceSourceRef(source_id=idx.document.source_id, span_id=span2.span_id, excerpt_hash=span2.excerpt_hash),),
    )
    add_claim(ld, c1, source_indexes={idx.document.source_id: idx})
    add_claim(ld, c2, source_indexes={idx.document.source_id: idx})
    detect_conflicts(ld)
    assert len(ld.conflicts) == 0


def test_conflict_preserves_all_candidates() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    values = [3, 5, 7]
    for v in values:
        c = _explicit(v, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
        add_claim(ld, c, source_indexes={idx.document.source_id: idx})
    detect_conflicts(ld)
    assert len(ld.conflicts) == 1
    conflict = next(iter(ld.conflicts.values()))
    assert len(conflict.claim_ids) == 3
    assert len(conflict.conflicting_values) == 3


def test_conflict_id_deterministic_across_redetection() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    c1 = _explicit(3, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    c2 = _explicit(5, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ld, c1, source_indexes={idx.document.source_id: idx})
    add_claim(ld, c2, source_indexes={idx.document.source_id: idx})
    detect_conflicts(ld)
    first = next(iter(ld.conflicts.values()))
    first_id = first.conflict_id
    detect_conflicts(ld)
    second = next(iter(ld.conflicts.values()))
    assert second.conflict_id == first_id


def test_conflict_cleared_when_values_reconcile() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    c1 = _explicit(3, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    c2 = _explicit(5, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ld, c1, source_indexes={idx.document.source_id: idx})
    add_claim(ld, c2, source_indexes={idx.document.source_id: idx})
    detect_conflicts(ld)
    assert len(ld.conflicts) == 1
    # Remove c2 by overwriting with a same-value claim.
    upsert_claim(ld, c2.model_copy(update={"value": 3}))
    detect_conflicts(ld)
    assert len(ld.conflicts) == 0


def test_conflict_does_not_overwrite_claims() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    c1 = _explicit(3, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    c2 = _explicit(5, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ld, c1, source_indexes={idx.document.source_id: idx})
    add_claim(ld, c2, source_indexes={idx.document.source_id: idx})
    detect_conflicts(ld)
    # Both original claims still present.
    assert c1.claim_id in ld.claims
    assert c2.claim_id in ld.claims
    assert ld.claims[c1.claim_id].value == 3
    assert ld.claims[c2.claim_id].value == 5


def test_confirmed_claim_immutability_blocks_value_change() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    c1 = _explicit(
        3,
        source_id=idx.document.source_id,
        span_id=span.span_id,
        excerpt_hash=span.excerpt_hash,
        confirmed=True,
        hc_id="hc_1",
    )
    add_claim(ld, c1, source_indexes={idx.document.source_id: idx})
    # Attempt to overwrite with a different value.
    c1_mutated = c1.model_copy(update={"value": 99, "confirmed_by_human": True, "human_confirmation_id": "hc_1"})
    # Re-trigger claim_id computation by passing through construction.
    forged = EvidenceClaim(
        claim_id="",
        subject=c1.subject,
        predicate=c1.predicate,
        value=99,
        status=EvidenceStatus.EXPLICIT,
        source_refs=c1.source_refs,
        confirmed_by_human=True,
        human_confirmation_id="hc_1",
    )
    assert forged.claim_id != c1.claim_id  # different value => different id
    with pytest.raises(PlanInvestigationIssue):
        # Try to overwrite the same claim_id with a new value via upsert of
        # an ID-forged claim.  We construct the mutation by suppressing the
        # claim_id validator's mismatch check.
        c1_with_new_value = c1.model_copy(update={"value": 99})
        # Bypass Pydantic revalidation by directly mutating the field that
        # would have re-recomputed the claim_id.  Then attempt upsert.
        object.__setattr__(c1_with_new_value, "value", 99)
        upsert_claim(ld, c1_with_new_value)


def test_confirmed_claim_upsert_idempotent() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    c1 = _explicit(
        3,
        source_id=idx.document.source_id,
        span_id=span.span_id,
        excerpt_hash=span.excerpt_hash,
        confirmed=True,
        hc_id="hc_1",
    )
    add_claim(ld, c1, source_indexes={idx.document.source_id: idx})
    # Re-upserting the EXACT same claim must succeed (idempotent).
    upsert_claim(ld, c1)
    assert ld.claims[c1.claim_id].value == 3
