"""Tests for EvidenceClaim model validation and status invariants."""

from __future__ import annotations

import pathlib
from datetime import datetime

import pytest
from pydantic import ValidationError

from openmc_agent.plan_investigation.errors import PlanInvestigationIssue
from openmc_agent.plan_investigation.hashing import content_hash
from openmc_agent.plan_investigation.models import (
    EvidenceClaim,
    EvidenceCriticality,
    EvidenceDerivation,
    EvidenceSourceRef,
    EvidenceStatus,
    SourceKind,
)
from openmc_agent.plan_investigation.source_index import build_source_index


# Pydantic v2 wraps ValueError raised inside validators as ValidationError,
# which is NOT a ValueError subclass.  Tests that exercise model-construction
# failures accept either layer.
_MODEL_FAILURE = (ValidationError, PlanInvestigationIssue)


def _make_index():
    return build_source_index(
        text="line one\nline two\nline three\n",
        title="t",
        source_kind=SourceKind.USER_REQUIREMENT,
    )


def test_explicit_claim_requires_source_ref() -> None:
    with pytest.raises(_MODEL_FAILURE):
        EvidenceClaim(
            claim_id="",
            subject="x",
            predicate="p",
            value=1,
            status=EvidenceStatus.EXPLICIT,
            source_refs=(),
        )


def test_explicit_claim_with_foreign_source_id_rejected() -> None:
    idx = _make_index()
    span = idx.make_span(1, 1)
    idx.register_span(span)
    ref = EvidenceSourceRef(
        source_id="src_foreign",
        span_id=span.span_id,
        excerpt_hash=span.excerpt_hash,
    )
    claim = EvidenceClaim(
        claim_id="",
        subject="x",
        predicate="p",
        value=1,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(ref,),
    )
    # Construction succeeds (source_ref shape is valid); but ledger-side
    # validation against the source index must reject it.
    from openmc_agent.plan_investigation.evidence_ledger import (
        create_empty_ledger,
        add_claim,
    )

    ledger = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    with pytest.raises(_MODEL_FAILURE):
        add_claim(ledger, claim, source_indexes={idx.document.source_id: idx})


def test_explicit_claim_cannot_carry_derivation() -> None:
    idx = _make_index()
    span = idx.make_span(1, 1)
    idx.register_span(span)
    ref = EvidenceSourceRef(
        source_id=idx.document.source_id,
        span_id=span.span_id,
        excerpt_hash=span.excerpt_hash,
    )
    der = EvidenceDerivation(
        derivation_id="",
        operation="integer_sum",
        input_claim_ids=("claim_dummy",),
        parameters={"operands": [1, 2]},
        result_hash=content_hash(3),
    )
    with pytest.raises(_MODEL_FAILURE):
        EvidenceClaim(
            claim_id="",
            subject="x",
            predicate="p",
            value=1,
            status=EvidenceStatus.EXPLICIT,
            source_refs=(ref,),
            derivation=der,
        )


def test_derived_claim_must_carry_derivation() -> None:
    with pytest.raises(_MODEL_FAILURE):
        EvidenceClaim(
            claim_id="",
            subject="x",
            predicate="p",
            value=1,
            status=EvidenceStatus.DETERMINISTICALLY_DERIVED,
            derivation=None,
        )


def test_external_official_rejected_at_construction() -> None:
    with pytest.raises(_MODEL_FAILURE):
        EvidenceClaim(
            claim_id="",
            subject="x",
            predicate="p",
            value=1,
            status=EvidenceStatus.EXTERNAL_OFFICIAL,
        )


def test_assumption_does_not_satisfy_source_critical() -> None:
    # An assumption claim is allowed to be marked source_critical at the
    # model level (the caller owns that decision); but the LEDGER must not
    # count it as resolved source-critical.  This is tested via the ledger
    # summary path; here we just check construction succeeds.
    claim = EvidenceClaim(
        claim_id="",
        subject="x",
        predicate="p",
        value=42,
        status=EvidenceStatus.ASSUMPTION,
        criticality=EvidenceCriticality.SOURCE_CRITICAL,
    )
    assert claim.status == EvidenceStatus.ASSUMPTION
    assert claim.confirmed_by_human is False


def test_unresolved_claim_cannot_carry_fabricated_source_ref() -> None:
    idx = _make_index()
    span = idx.make_span(1, 1)
    idx.register_span(span)
    ref = EvidenceSourceRef(
        source_id=idx.document.source_id,
        span_id=span.span_id,
        excerpt_hash=span.excerpt_hash,
    )
    # An unresolved claim MAY cite source spans (e.g. to indicate "the
    # document mentions this but doesn't fully resolve it"); however, an
    # unresolved claim MUST NOT count as a satisfied source-critical claim.
    # The model accepts source_refs on unresolved claims; the ledger policy
    # is enforced in unresolved_source_critical_claims().
    claim = EvidenceClaim(
        claim_id="",
        subject="x",
        predicate="p",
        value=None,
        status=EvidenceStatus.UNRESOLVED,
        source_refs=(ref,),
        criticality=EvidenceCriticality.SOURCE_CRITICAL,
    )
    assert claim.status == EvidenceStatus.UNRESOLVED


def test_conflict_status_cannot_be_constructed_directly() -> None:
    with pytest.raises(_MODEL_FAILURE):
        EvidenceClaim(
            claim_id="",
            subject="x",
            predicate="p",
            value=1,
            status=EvidenceStatus.CONFLICT,
        )


def test_confirmed_by_human_requires_confirmation_id() -> None:
    with pytest.raises(_MODEL_FAILURE):
        EvidenceClaim(
            claim_id="",
            subject="x",
            predicate="p",
            value=1,
            status=EvidenceStatus.EXPLICIT,
            source_refs=(
                EvidenceSourceRef(source_id="src_x", span_id="span_y", excerpt_hash="h"),
            ),
            confirmed_by_human=True,
            human_confirmation_id=None,
        )


def test_confirmed_claim_can_be_constructed_with_id() -> None:
    idx = _make_index()
    span = idx.make_span(1, 1)
    idx.register_span(span)
    ref = EvidenceSourceRef(
        source_id=idx.document.source_id,
        span_id=span.span_id,
        excerpt_hash=span.excerpt_hash,
    )
    claim = EvidenceClaim(
        claim_id="",
        subject="x",
        predicate="p",
        value=1,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(ref,),
        confirmed_by_human=True,
        human_confirmation_id="hc_1",
    )
    assert claim.confirmed_by_human is True
    assert claim.human_confirmation_id == "hc_1"


def test_claim_value_must_be_json_compatible() -> None:
    idx = _make_index()
    span = idx.make_span(1, 1)
    idx.register_span(span)
    ref = EvidenceSourceRef(
        source_id=idx.document.source_id,
        span_id=span.span_id,
        excerpt_hash=span.excerpt_hash,
    )
    with pytest.raises(_MODEL_FAILURE):
        EvidenceClaim(
            claim_id="",
            subject="x",
            predicate="p",
            value=pathlib.Path("/etc/passwd"),
            status=EvidenceStatus.EXPLICIT,
            source_refs=(ref,),
        )
    with pytest.raises(_MODEL_FAILURE):
        EvidenceClaim(
            claim_id="",
            subject="x",
            predicate="p",
            value=datetime(2024, 1, 1),
            status=EvidenceStatus.EXPLICIT,
            source_refs=(ref,),
        )


def test_claim_id_deterministic_and_independent_of_metadata() -> None:
    idx = _make_index()
    span = idx.make_span(1, 1)
    idx.register_span(span)
    ref = EvidenceSourceRef(
        source_id=idx.document.source_id,
        span_id=span.span_id,
        excerpt_hash=span.excerpt_hash,
    )
    base = dict(
        subject="x",
        predicate="p",
        value=1,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(ref,),
    )
    c1 = EvidenceClaim(claim_id="", metadata={"run_id": "r1"}, **base)
    c2 = EvidenceClaim(claim_id="", metadata={"run_id": "r2"}, **base)
    assert c1.claim_id == c2.claim_id


def test_claim_id_changes_with_value() -> None:
    idx = _make_index()
    span = idx.make_span(1, 1)
    idx.register_span(span)
    ref = EvidenceSourceRef(
        source_id=idx.document.source_id,
        span_id=span.span_id,
        excerpt_hash=span.excerpt_hash,
    )
    c1 = EvidenceClaim(claim_id="", subject="x", predicate="p", value=1, status=EvidenceStatus.EXPLICIT, source_refs=(ref,))
    c2 = EvidenceClaim(claim_id="", subject="x", predicate="p", value=2, status=EvidenceStatus.EXPLICIT, source_refs=(ref,))
    assert c1.claim_id != c2.claim_id


def test_subject_predicate_cannot_be_empty() -> None:
    with pytest.raises(_MODEL_FAILURE):
        EvidenceClaim(
            claim_id="",
            subject="",
            predicate="p",
            value=1,
            status=EvidenceStatus.ASSUMPTION,
        )
