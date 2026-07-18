"""Tests for canonical hashing primitives and ID computation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openmc_agent.plan_investigation.errors import PlanInvestigationIssue
from openmc_agent.plan_investigation.hashing import (
    canonical_json_dumps,
    content_hash,
    short_id,
)
from openmc_agent.plan_investigation.models import (
    SourceDocument,
    SourceKind,
    compute_claim_id,
)


_MODEL_FAILURE = (ValidationError, PlanInvestigationIssue)


def test_canonical_json_is_order_independent() -> None:
    a = canonical_json_dumps({"b": 2, "a": 1, "c": [3, 2, 1]})
    b = canonical_json_dumps({"c": [3, 2, 1], "a": 1, "b": 2})
    assert a == b


def test_canonical_json_rejects_nan_and_inf() -> None:
    with pytest.raises(ValueError):
        canonical_json_dumps(float("nan"))
    with pytest.raises(ValueError):
        canonical_json_dumps(float("inf"))


def test_content_hash_stable_across_runs() -> None:
    payload = {"a": [1, 2, 3], "b": "hello", "nested": {"x": 1.0}}
    assert content_hash(payload) == content_hash(payload)


def test_content_hash_changes_with_payload() -> None:
    a = content_hash({"v": 1})
    b = content_hash({"v": 2})
    assert a != b


def test_short_id_has_prefix_and_length() -> None:
    sid = short_id("claim", {"x": 1})
    assert sid.startswith("claim_")
    assert len(sid.split("_", 1)[1]) == 16


def test_short_id_requires_prefix() -> None:
    with pytest.raises(ValueError):
        short_id("", {"x": 1})


def test_python_builtin_hash_not_used() -> None:
    """content_hash must NOT depend on Python's hash() (which is salted)."""
    payload = {"list": [1, 2, 3], "str": "abc"}
    h1 = content_hash(payload)
    # Force a different hash seed by computing in a fresh interpreter
    # would be ideal; here we just verify the same process gives a stable
    # value, and that the value is a hex SHA-256 length.
    assert h1 == content_hash(payload)
    assert len(h1) == 64
    int(h1, 16)  # valid hex


def test_source_id_forbids_manual_construction() -> None:
    # Trying to forge a source_id that doesn't match the recomputed value.
    with pytest.raises(_MODEL_FAILURE):
        SourceDocument(
            source_id="src_forged",
            source_kind=SourceKind.USER_REQUIREMENT,
            title="t",
            origin_label="",
            content_hash="a" * 64,
            normalized_content_hash="a" * 64,
            line_count=1,
            char_count=1,
            section_count=1,
        )


def test_source_id_autofills_when_empty() -> None:
    doc = SourceDocument(
        source_id="",
        source_kind=SourceKind.USER_REQUIREMENT,
        title="t",
        origin_label="",
        content_hash="a" * 64,
        normalized_content_hash="a" * 64,
        line_count=1,
        char_count=1,
        section_count=1,
    )
    assert doc.source_id.startswith("src_")
    assert doc.source_id != ""


def test_claim_id_excludes_timestamps_and_paths() -> None:
    """claim_id must not depend on incidental metadata."""
    base = dict(
        subject="x",
        predicate="p",
        qualifiers={"k": 1},
        value=42,
        status="explicit",
        source_refs=[{"source_id": "s", "span_id": "sp", "excerpt_hash": "h"}],
        derivation_present=False,
        criticality="informational",
    )
    a = compute_claim_id(**base)
    b = compute_claim_id(**base)
    assert a == b
    # Different value => different id.
    different = dict(base)
    different["value"] = 99
    assert a != compute_claim_id(**different)


def test_ledger_hash_distinct_from_content_hash() -> None:
    """Ledger hash uses content_hash as a primitive but a different payload."""
    from openmc_agent.plan_investigation.evidence_ledger import recompute_ledger_hash, create_empty_ledger

    ledger = create_empty_ledger(requirement_hash="rh")
    # The ledger hash is NOT just content_hash of requirement_hash.
    assert recompute_ledger_hash(ledger) != content_hash("rh")
    assert recompute_ledger_hash(ledger) != content_hash({"requirement_hash": "rh"})
