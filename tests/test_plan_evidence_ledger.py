"""Tests for PlanningEvidenceLedger lifecycle, hashing, and queries."""

from __future__ import annotations

import json

import pytest

from openmc_agent.plan_investigation.errors import PlanInvestigationIssue
from openmc_agent.plan_investigation.evidence_ledger import (
    PlanningEvidenceLedger,
    add_claim,
    claims_for_json_path,
    claims_for_patch_type,
    create_empty_ledger,
    detect_conflicts,
    finalize_ledger,
    find_claims,
    find_stale_derived_claims,
    get_claim_by_id,
    ledger_summary,
    recompute_ledger_hash,
    unresolved_source_critical_claims,
    upsert_claim,
)
from openmc_agent.plan_investigation.hashing import content_hash
from openmc_agent.plan_investigation.models import (
    EvidenceClaim,
    EvidenceCriticality,
    EvidenceSourceRef,
    EvidenceStatus,
    SourceKind,
)
from openmc_agent.plan_investigation.source_index import build_source_index


def _idx():
    idx = build_source_index(
        text="alpha\nbeta\ngamma\n",
        title="t",
        source_kind=SourceKind.USER_REQUIREMENT,
    )
    span = idx.make_span(1, 1)
    idx.register_span(span)
    return idx, span


def _explicit(value=1, *, subject="x", predicate="p", criticality=EvidenceCriticality.INFORMATIONAL, source_id=None, span_id=None, excerpt_hash=None):
    return EvidenceClaim(
        claim_id="",
        subject=subject,
        predicate=predicate,
        value=value,
        status=EvidenceStatus.EXPLICIT,
        criticality=criticality,
        source_refs=(
            EvidenceSourceRef(source_id=source_id, span_id=span_id, excerpt_hash=excerpt_hash),
        ),
    )


def test_duplicate_claim_rejected() -> None:
    idx, span = _idx()
    ledger = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    claim = _explicit(source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ledger, claim, source_indexes={idx.document.source_id: idx})
    with pytest.raises(PlanInvestigationIssue):
        add_claim(ledger, claim, source_indexes={idx.document.source_id: idx})


def test_same_semantic_key_different_value_kept_separately() -> None:
    idx, span = _idx()
    ledger = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    c1 = _explicit(value=1, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    c2 = _explicit(value=2, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ledger, c1, source_indexes={idx.document.source_id: idx})
    add_claim(ledger, c2, source_indexes={idx.document.source_id: idx})
    assert len(ledger.claims) == 2


def test_input_order_does_not_affect_ledger_hash() -> None:
    idx, span = _idx()

    def build():
        ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
        c1 = _explicit(value=1, subject="a", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
        c2 = _explicit(value=2, subject="b", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
        return ld, c1, c2

    ld1, c1, c2 = build()
    add_claim(ld1, c1, source_indexes={idx.document.source_id: idx})
    add_claim(ld1, c2, source_indexes={idx.document.source_id: idx})
    finalize_ledger(ld1)

    ld2, c1b, c2b = build()
    add_claim(ld2, c2b, source_indexes={idx.document.source_id: idx})
    add_claim(ld2, c1b, source_indexes={idx.document.source_id: idx})
    finalize_ledger(ld2)

    assert ld1.ledger_hash == ld2.ledger_hash


def test_semantic_field_change_changes_hash() -> None:
    idx, span = _idx()
    ld1 = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    ld2 = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    add_claim(ld1, _explicit(value=1, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash), source_indexes={idx.document.source_id: idx})
    add_claim(ld2, _explicit(value=99, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash), source_indexes={idx.document.source_id: idx})
    finalize_ledger(ld1)
    finalize_ledger(ld2)
    assert ld1.ledger_hash != ld2.ledger_hash


def test_json_roundtrip_preserves_hash() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    add_claim(ld, _explicit(source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash), source_indexes={idx.document.source_id: idx})
    finalize_ledger(ld)
    payload = ld.model_dump(mode="json")
    payload_json = json.dumps(payload, sort_keys=True)
    restored = PlanningEvidenceLedger.model_validate(json.loads(payload_json))
    assert restored.ledger_hash == ld.ledger_hash
    assert recompute_ledger_hash(restored) == ld.ledger_hash


def test_metadata_does_not_affect_ledger_hash() -> None:
    idx, span = _idx()
    ld1 = create_empty_ledger(requirement_hash="rh", source_indexes=[idx], metadata={"run_id": "r1"})
    ld2 = create_empty_ledger(requirement_hash="rh", source_indexes=[idx], metadata={"run_id": "r2"})
    finalize_ledger(ld1)
    finalize_ledger(ld2)
    assert ld1.ledger_hash == ld2.ledger_hash


def test_find_claims_filter() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    c1 = _explicit(subject="a", predicate="p1", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    c2 = _explicit(subject="a", predicate="p2", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    c3 = _explicit(subject="b", predicate="p1", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    for c in (c1, c2, c3):
        add_claim(ld, c, source_indexes={idx.document.source_id: idx})
    assert len(find_claims(ld, subject="a")) == 2
    assert len(find_claims(ld, subject="a", predicate="p1")) == 1
    assert len(find_claims(ld, status=EvidenceStatus.EXPLICIT)) == 3


def test_claims_for_patch_type_and_json_path() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    c1 = EvidenceClaim(
        claim_id="",
        subject="x",
        predicate="p",
        value=1,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(EvidenceSourceRef(source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash),),
        required_by_patch_types=("materials", "universes"),
        required_by_json_paths=("materials.density",),
    )
    add_claim(ld, c1, source_indexes={idx.document.source_id: idx})
    assert len(claims_for_patch_type(ld, "materials")) == 1
    assert len(claims_for_patch_type(ld, "universes")) == 1
    assert len(claims_for_patch_type(ld, "settings")) == 0
    assert len(claims_for_json_path(ld, "materials.density")) == 1
    assert len(claims_for_json_path(ld, "core.layout")) == 0


def test_get_claim_by_id() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    c = _explicit(source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ld, c, source_indexes={idx.document.source_id: idx})
    assert get_claim_by_id(ld, c.claim_id) is c
    assert get_claim_by_id(ld, "claim_missing") is None


def test_unresolved_source_critical_summary() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    resolved = EvidenceClaim(
        claim_id="",
        subject="x",
        predicate="resolved",
        value=1,
        status=EvidenceStatus.EXPLICIT,
        criticality=EvidenceCriticality.SOURCE_CRITICAL,
        source_refs=(EvidenceSourceRef(source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash),),
    )
    unresolved = EvidenceClaim(
        claim_id="",
        subject="y",
        predicate="unresolved",
        value=None,
        status=EvidenceStatus.UNRESOLVED,
        criticality=EvidenceCriticality.SOURCE_CRITICAL,
    )
    add_claim(ld, resolved, source_indexes={idx.document.source_id: idx})
    add_claim(ld, unresolved)
    finalize_ledger(ld)
    summary = ledger_summary(ld)
    assert summary.source_critical_count == 2
    assert summary.source_critical_unresolved_count == 1
    assert summary.source_critical_resolved_count == 1
    assert len(unresolved_source_critical_claims(ld)) == 1


def test_recompute_ledger_hash_matches_finalize() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    add_claim(ld, _explicit(source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash), source_indexes={idx.document.source_id: idx})
    finalize_ledger(ld)
    assert ld.ledger_hash == recompute_ledger_hash(ld)


def test_empty_ledger_has_stable_hash() -> None:
    idx, _ = _idx()
    ld1 = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    ld2 = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    finalize_ledger(ld1)
    finalize_ledger(ld2)
    assert ld1.ledger_hash == ld2.ledger_hash
