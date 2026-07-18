"""Tests for the search_source_index tool."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.evidence_ledger import (
    add_claim,
    create_empty_ledger,
    find_claims,
)
from openmc_agent.plan_investigation.models import (
    EvidenceClaim,
    EvidenceSourceRef,
    EvidenceStatus,
    SourceKind,
)
from openmc_agent.plan_investigation.source_index import build_source_index
from openmc_agent.plan_investigation.tool_models import InvestigationToolRequest
from openmc_agent.plan_investigation.tool_registry import (
    TOOL_NAME_SEARCH_SOURCE_INDEX,
    ToolExecutionContext,
    build_default_step2_registry,
)


def _ctx(text="alpha\nbeta\ngamma\nalpha\n"):
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    return idx, ld, ToolExecutionContext(source_indexes={idx.document.source_id: idx}, ledger=ld)


def test_keyword_hit_returns_spans() -> None:
    idx, ld, ctx = _ctx()
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "alpha"},
    )
    res = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    assert res.ok
    assert res.result["total_hits"] == 2
    spans = res.result["spans"]
    assert spans[0]["start_line"] == 1
    assert spans[1]["start_line"] == 4


def test_line_numbers_are_stable() -> None:
    idx, ld, ctx = _ctx("a\nb\nc\na\n")
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "a"},
    )
    res = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    assert [s["start_line"] for s in res.result["spans"]] == [1, 4]


def test_deterministic_ordering_across_runs() -> None:
    idx, ld, ctx = _ctx("foo\nbar\nfoo\nbaz\nfoo\n")
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "foo"},
    )
    res1 = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    # Run a second time; spans must be in the same order even though
    # they may now be deduped at the claim level.
    res2 = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    lines1 = [s["start_line"] for s in res1.result["spans"]]
    lines2 = [s["start_line"] for s in res2.result["spans"]]
    assert lines1 == lines2 == [1, 3, 5]


def test_empty_result_set() -> None:
    idx, ld, ctx = _ctx("alpha\nbeta\n")
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "zzz"},
    )
    res = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    assert res.ok
    assert res.result["total_hits"] == 0
    assert res.result["spans"] == []
    assert res.evidence_claim_ids == ()


def test_keyword_filter_narrows_results() -> None:
    idx, ld, ctx = _ctx("fuel pin\ncoolant pin\nfuel temperature\n")
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "pin", "keywords": ["fuel"]},
    )
    res = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    # Only line 1 contains "pin" AND "fuel".
    assert res.result["total_hits"] == 1
    assert res.result["spans"][0]["start_line"] == 1


def test_each_hit_produces_evidence_claim() -> None:
    idx, ld, ctx = _ctx("alpha\nbeta\nalpha\n")
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "alpha"},
    )
    res = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    assert len(res.evidence_claim_ids) == 2
    # Claims are queryable in the ledger.
    matches = find_claims(ld, predicate="search_hit")
    assert len(matches) == 2


def test_each_claim_has_source_ref_pointing_to_real_span() -> None:
    idx, ld, ctx = _ctx("alpha\n")
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "alpha"},
    )
    res = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    assert len(res.source_refs) == 1
    ref = res.source_refs[0]
    # The ref's span must validate against the index.
    idx.validate_source_ref(ref)


def test_empty_query_returns_failure() -> None:
    idx, ld, ctx = _ctx("alpha\n")
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": ""},
    )
    res = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    assert not res.ok
    assert "tool_argument_invalid" in res.error_codes[0]


def test_unknown_source_id_returns_failure() -> None:
    idx, ld, ctx = _ctx("alpha\n")
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "alpha", "source_id": "src_foreign"},
    )
    res = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    assert not res.ok


def test_max_results_truncates() -> None:
    idx, ld, ctx = _ctx("a\na\na\na\na\n")
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "a", "max_results": 2},
    )
    res = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    assert res.result["total_hits"] == 2
    assert res.result["truncated"] is True


def test_execution_hash_deterministic() -> None:
    idx1, _, ctx1 = _ctx("alpha\n")
    idx2, _, ctx2 = _ctx("alpha\n")
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "alpha"},
    )
    res1 = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx1)
    res2 = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx2)
    assert res1.execution_hash == res2.execution_hash


def test_no_free_text_prose_in_result() -> None:
    """The result payload must be structured; no natural language sentences."""
    idx, ld, ctx = _ctx("alpha\n")
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "alpha"},
    )
    res = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    # The result must not contain any "based on..." / "the document..."
    # style prose keys.
    serialized = repr(res.result)
    assert "based on" not in serialized.lower()
    assert "the document" not in serialized.lower()
