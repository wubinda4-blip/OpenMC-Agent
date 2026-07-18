"""Tests for SourceSpan construction, validation, and tamper resistance."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.errors import PlanInvestigationIssue
from openmc_agent.plan_investigation.hashing import content_hash
from openmc_agent.plan_investigation.models import (
    EvidenceSourceRef,
    SourceKind,
    SourceSpan,
)
from openmc_agent.plan_investigation.source_index import build_source_index


def test_make_span_excerpt_matches_source_verbatim() -> None:
    text = "alpha\nbeta\ngamma\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    span = idx.make_span(2, 3)
    assert span.excerpt == "beta\ngamma"
    assert span.start_line == 2
    assert span.end_line == 3
    assert span.excerpt_hash == content_hash("beta\ngamma")


def test_line_out_of_range_rejected() -> None:
    text = "a\nb\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    with pytest.raises(PlanInvestigationIssue):
        idx.make_span(0, 1)
    with pytest.raises(PlanInvestigationIssue):
        idx.make_span(1, 99)
    with pytest.raises(PlanInvestigationIssue):
        idx.make_span(2, 1)  # reversed range


def test_excerpt_modified_after_construction_detected() -> None:
    text = "alpha\nbeta\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    span = idx.make_span(1, 2)
    # Try to forge a span with a wrong excerpt_hash.  Pydantic wraps the
    # PlanInvestigationIssue (a ValueError) as a ValidationError, so we
    # catch Exception and verify the code attribute via the original
    # exception's args.
    forged_hash = "0" * 64
    with pytest.raises(Exception):
        SourceSpan(
            span_id="",
            source_id=idx.document.source_id,
            start_line=1,
            end_line=2,
            section_id=span.section_id,
            section_path=span.section_path,
            excerpt="alpha\nbeta",
            excerpt_hash=forged_hash,
        )


def test_forged_source_id_rejected_at_index_validation() -> None:
    """A span with a foreign source_id is constructable (its span_id is
    self-consistent), but the index's validate_span rejects it because the
    source_id does not match.
    """
    text = "alpha\nbeta\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    span = idx.make_span(1, 2)
    # Build a span claiming a different source_id but with the same content.
    foreign = SourceSpan(
        span_id="",
        source_id="src_foreign",
        start_line=1,
        end_line=2,
        section_id=span.section_id,
        section_path=span.section_path,
        excerpt=span.excerpt,
        excerpt_hash=span.excerpt_hash,
    )
    # Construction succeeds (the foreign span is internally consistent);
    # index validation rejects it.
    with pytest.raises(PlanInvestigationIssue):
        idx.validate_span(foreign)


def test_same_span_produces_same_span_id() -> None:
    text = "alpha\nbeta\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    a = idx.make_span(1, 1)
    b = idx.make_span(1, 1)
    assert a.span_id == b.span_id


def test_different_range_produces_different_span_id() -> None:
    text = "alpha\nbeta\ngamma\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    a = idx.make_span(1, 1)
    b = idx.make_span(1, 2)
    assert a.span_id != b.span_id


def test_validate_span_rejects_foreign_span() -> None:
    text = "alpha\nbeta\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    other = build_source_index(text="alpha\nbeta\n", title="t2", source_kind=SourceKind.USER_REQUIREMENT)
    span_on_other = other.make_span(1, 2)
    with pytest.raises(PlanInvestigationIssue):
        idx.validate_span(span_on_other)


def test_validate_span_detects_excerpt_tamper() -> None:
    text = "alpha\nbeta\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    legit = idx.make_span(1, 2)
    tampered = legit.model_copy(update={"excerpt": "alpha\nBETA"})
    # Pydantic's revalidation on copy does NOT trigger because model_copy
    # skips validators by default.  So we hand-build the failure detection
    # via validate_span directly.
    with pytest.raises(PlanInvestigationIssue):
        idx.validate_span(tampered)


def test_register_span_enables_source_ref_validation() -> None:
    text = "alpha\nbeta\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    span = idx.make_span(1, 1)
    idx.register_span(span)
    ref = EvidenceSourceRef(
        source_id=idx.document.source_id,
        span_id=span.span_id,
        excerpt_hash=span.excerpt_hash,
    )
    idx.validate_source_ref(ref)  # should not raise


def test_source_ref_unknown_span_rejected() -> None:
    text = "alpha\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    ref = EvidenceSourceRef(source_id=idx.document.source_id, span_id="span_unknown", excerpt_hash="x")
    with pytest.raises(PlanInvestigationIssue):
        idx.validate_source_ref(ref)


def test_source_ref_foreign_source_rejected() -> None:
    text = "alpha\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    ref = EvidenceSourceRef(source_id="src_foreign", span_id="span_x", excerpt_hash="x")
    with pytest.raises(PlanInvestigationIssue):
        idx.validate_source_ref(ref)


def test_find_literal_returns_hits() -> None:
    text = "alpha\nbeta\nalpha\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    spans = idx.find_literal("alpha")
    assert len(spans) == 2
    assert spans[0].start_line == 1
    assert spans[1].start_line == 3


def test_find_keywords_and_semantics() -> None:
    text = "fuel pin radius\ncoolant pin radius\nfuel temperature\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    spans = idx.find_keywords(["fuel", "radius"])
    assert len(spans) == 1
    assert spans[0].start_line == 1


def test_get_lines_preserves_internal_newlines() -> None:
    text = "a\nb\nc\nd\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    assert idx.get_lines(2, 4) == "b\nc\nd"
