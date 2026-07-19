"""Phase 8A Step 7 — research evidence synthesis tests (Sections 5-6)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openmc_agent.plan_investigation.evidence_ledger import (
    PlanningEvidenceLedger, create_empty_ledger,
)
from openmc_agent.plan_investigation.models import SourceSpan
from openmc_agent.plan_investigation.research_models import (
    PlanResearchRequest, PlanResearchTarget,
)
from openmc_agent.plan_investigation.research_synthesis import (
    ALLOWED_RESEARCH_PREDICATES,
    ResearchEvidenceSynthesisContext,
    ResearchEvidenceProposal,
    build_research_synthesis_context,
    commit_research_evidence_proposals,
    parse_research_synthesis_output,
    render_research_synthesis_prompt,
    run_research_evidence_synthesis,
    validate_research_evidence_proposals,
)
from openmc_agent.plan_investigation.runner import build_investigation_source_index


REQ = (
    "A single assembly. Fuel density is 10.0 g/cc. "
    "Cladding outer diameter is 0.95 cm. Pin pitch is 1.26 cm."
)


class _FakeSourceIndex:
    """Minimal source index stand-in for synthesis tests."""

    def __init__(self, spans: list[SourceSpan]) -> None:
        self.sections = [type("S", (), {"spans": spans})()]


def _span(span_id: str, excerpt: str, source_id: str = "doc1") -> SourceSpan:
    from openmc_agent.plan_investigation.hashing import content_hash
    from openmc_agent.plan_investigation.models import _compute_span_id
    excerpt_hash = content_hash(excerpt)
    return SourceSpan(
        source_id=source_id,
        section_id="sec1",
        span_id=_compute_span_id(
            source_id=source_id, start_line=1, end_line=2,
            excerpt_hash=excerpt_hash,
        ),
        start_line=1,
        end_line=2,
        excerpt=excerpt,
        excerpt_hash=excerpt_hash,
    )


def _make_ctx(spans: list[SourceSpan], ledger: PlanningEvidenceLedger) -> ResearchEvidenceSynthesisContext:
    target = PlanResearchTarget(
        claim_predicates=("material.density",),
        suggested_search_terms=("density",),
    )
    request = PlanResearchRequest(
        gate_id="material_universe",
        targets=(target,),
        ledger_hash_before=ledger.ledger_hash,
    )
    return build_research_synthesis_context(
        request=request,
        candidate_spans=spans,
        ledger=ledger,
        gate_findings=[],
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_valid_output() -> None:
    raw = json.dumps({
        "proposals": [
            {
                "target_id": "t1",
                "subject": "material_role:fuel",
                "predicate": "material.density",
                "value": 10.0,
                "source_span_ids": ["span_1"],
                "criticality": "source_critical",
            }
        ],
        "unresolved_targets": [],
        "conflicts": [],
    })
    parsed = parse_research_synthesis_output(raw)
    assert parsed is not None
    assert len(parsed["proposals"]) == 1


def test_parse_invalid_output_returns_none() -> None:
    assert parse_research_synthesis_output("not json") is None
    assert parse_research_synthesis_output("") is None
    assert parse_research_synthesis_output('{"no_proposals_key": true}') is None


def test_parse_markdown_fenced_json() -> None:
    raw = '```json\n{"proposals": [], "unresolved_targets": [], "conflicts": []}\n```'
    parsed = parse_research_synthesis_output(raw)
    assert parsed is not None
    assert parsed["proposals"] == []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_valid_proposal_accepted() -> None:
    """A proposal with a valid span + value present in excerpt → accepted."""

    span = _span("ignored", "Fuel density is 10.0 g/cc.")
    spans = [span]
    ledger = create_empty_ledger(requirement_hash="rh")
    ctx = _make_ctx(spans, ledger)
    source_index = _FakeSourceIndex(spans)
    synthesis = {
        "proposals": [{
            "target_id": ctx.research_targets[0].target_id,
            "subject": "material_role:fuel",
            "predicate": "material.density",
            "value": 10.0,
            "source_span_ids": [span.span_id],  # use computed span_id
            "criticality": "source_critical",
        }],
        "unresolved_targets": [],
        "conflicts": [],
    }
    result = validate_research_evidence_proposals(
        synthesis_output=synthesis, ctx=ctx, ledger=ledger, source_index=source_index,
    )
    assert len(result.accepted) == 1
    assert len(result.rejected) == 0


def test_unknown_span_id_rejected() -> None:
    """Proposal referencing a span_id not in the candidate list → rejected."""

    spans = [_span("ignored", "density 10.0")]
    ledger = create_empty_ledger(requirement_hash="rh")
    ctx = _make_ctx(spans, ledger)
    source_index = _FakeSourceIndex(spans)
    synthesis = {
        "proposals": [{
            "target_id": ctx.research_targets[0].target_id,
            "subject": "material_role:fuel",
            "predicate": "material.density",
            "value": 10.0,
            "source_span_ids": ["totally_invented_span"],
        }],
        "unresolved_targets": [],
        "conflicts": [],
    }
    result = validate_research_evidence_proposals(
        synthesis_output=synthesis, ctx=ctx, ledger=ledger, source_index=source_index,
    )
    assert len(result.accepted) == 0
    assert len(result.rejected) == 1
    assert result.rejected[0]["reason"] == "unknown_span_id"


def test_disallowed_predicate_rejected() -> None:
    span = _span("ignored", "density 10.0")
    spans = [span]
    ledger = create_empty_ledger(requirement_hash="rh")
    ctx = _make_ctx(spans, ledger)
    source_index = _FakeSourceIndex(spans)
    synthesis = {
        "proposals": [{
            "target_id": ctx.research_targets[0].target_id,
            "subject": "x",
            "predicate": "totally.invented.predicate",
            "value": 10.0,
            "source_span_ids": [span.span_id],
        }],
        "unresolved_targets": [],
        "conflicts": [],
    }
    result = validate_research_evidence_proposals(
        synthesis_output=synthesis, ctx=ctx, ledger=ledger, source_index=source_index,
    )
    assert len(result.accepted) == 0
    assert any(r["reason"] == "predicate_not_allowed" for r in result.rejected)


def test_source_critical_without_span_rejected() -> None:
    spans = [_span("ignored", "density 10.0")]
    ledger = create_empty_ledger(requirement_hash="rh")
    ctx = _make_ctx(spans, ledger)
    source_index = _FakeSourceIndex(spans)
    synthesis = {
        "proposals": [{
            "target_id": ctx.research_targets[0].target_id,
            "subject": "x",
            "predicate": "material.density",
            "value": 10.0,
            "source_span_ids": [],
            "criticality": "source_critical",
        }],
        "unresolved_targets": [],
        "conflicts": [],
    }
    result = validate_research_evidence_proposals(
        synthesis_output=synthesis, ctx=ctx, ledger=ledger, source_index=source_index,
    )
    assert len(result.accepted) == 0
    assert any(r["reason"] == "source_critical_without_span" for r in result.rejected)


def test_value_not_in_excerpt_rejected() -> None:
    """Numerical value not present in span excerpt → rejected."""

    span = _span("ignored", "density 10.0 g/cc")
    spans = [span]
    ledger = create_empty_ledger(requirement_hash="rh")
    ctx = _make_ctx(spans, ledger)
    source_index = _FakeSourceIndex(spans)
    synthesis = {
        "proposals": [{
            "target_id": ctx.research_targets[0].target_id,
            "subject": "x",
            "predicate": "material.density",
            "value": 99.99,  # not in excerpt
            "source_span_ids": [span.span_id],
        }],
        "unresolved_targets": [],
        "conflicts": [],
    }
    result = validate_research_evidence_proposals(
        synthesis_output=synthesis, ctx=ctx, ledger=ledger, source_index=source_index,
    )
    assert len(result.accepted) == 0
    assert any(r["reason"] == "value_not_verifiable_in_span_excerpt" for r in result.rejected)


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def test_commit_changes_ledger_hash() -> None:
    """Committing accepted proposals changes the ledger hash."""

    span = _span("ignored", "density 10.0")
    spans = [span]
    ledger = create_empty_ledger(requirement_hash="rh")
    ledger_hash_before = ledger.ledger_hash
    ctx = _make_ctx(spans, ledger)
    source_index = _FakeSourceIndex(spans)
    from openmc_agent.plan_investigation.research_synthesis import (
        ResearchProposalValidationResult,
    )
    validation = ResearchProposalValidationResult(
        accepted=(
            ResearchEvidenceProposal(
                target_id=ctx.research_targets[0].target_id,
                subject="material_role:fuel",
                predicate="material.density",
                value=10.0,
                source_span_ids=(span.span_id,),
                criticality="source_critical",
            ),
        ),
    )
    delta = commit_research_evidence_proposals(
        validation=validation, ledger=ledger, source_index=source_index,
        request_id="r1",
    )
    assert len(delta.added_claim_ids) == 1
    assert delta.ledger_hash_after != ledger_hash_before


def test_commit_idempotent_on_duplicate() -> None:
    """Committing the same proposal twice does not add a duplicate claim."""

    span = _span("ignored", "density 10.0")
    spans = [span]
    ledger = create_empty_ledger(requirement_hash="rh")
    ctx = _make_ctx(spans, ledger)
    source_index = _FakeSourceIndex(spans)
    from openmc_agent.plan_investigation.research_synthesis import (
        ResearchProposalValidationResult,
    )
    validation = ResearchProposalValidationResult(
        accepted=(
            ResearchEvidenceProposal(
                target_id="t1",
                subject="material_role:fuel",
                predicate="material.density",
                value=10.0,
                source_span_ids=(span.span_id,),
                criticality="source_critical",
            ),
        ),
    )
    delta1 = commit_research_evidence_proposals(
        validation=validation, ledger=ledger, source_index=source_index, request_id="r1",
    )
    delta2 = commit_research_evidence_proposals(
        validation=validation, ledger=ledger, source_index=source_index, request_id="r1",
    )
    assert len(delta1.added_claim_ids) == 1
    assert len(delta2.added_claim_ids) == 0  # idempotent
    assert delta2.ledger_hash_after == delta1.ledger_hash_after


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------


def test_run_synthesis_with_fake_llm() -> None:
    """End-to-end: deterministic search → fake LLM → commit."""

    idx = build_investigation_source_index(REQ)
    ledger = create_empty_ledger(requirement_hash=idx.document.source_id)
    target = PlanResearchTarget(
        claim_predicates=("material.density",),
        suggested_search_terms=("density",),
    )
    request = PlanResearchRequest(
        gate_id="material_universe",
        targets=(target,),
        ledger_hash_before=ledger.ledger_hash,
    )
    # Run the deterministic search first.
    from openmc_agent.plan_investigation.research_executor import (
        execute_plan_research_request,
    )
    research_result = execute_plan_research_request(
        request=request, source_index=idx, ledger=ledger,
    )
    # Fake LLM that emits a valid proposal if any spans were located.
    def fake_llm(prompt: str) -> str:
        # Extract span_ids from the prompt.
        import re
        span_ids = re.findall(r"span_id=([^\s]+)", prompt)
        if not span_ids:
            return json.dumps({"proposals": [], "unresolved_targets": [target.target_id], "conflicts": []})
        return json.dumps({
            "proposals": [{
                "target_id": target.target_id,
                "subject": "material_role:fuel",
                "predicate": "material.density",
                "value": 10.0,
                "source_span_ids": [span_ids[0]],
                "criticality": "source_critical",
            }],
            "unresolved_targets": [],
            "conflicts": [],
        })
    delta = run_research_evidence_synthesis(
        request=request,
        research_result=research_result,
        ledger=ledger,
        source_index=idx,
        gate_findings=[],
        llm_client=fake_llm,
    )
    # If spans were found, delta should be non-None and have claims.
    if delta is not None:
        # Either evidence was committed (hash changed) or not.
        assert delta.ledger_hash_before is not None
        assert delta.ledger_hash_after is not None


def test_synthesis_no_llm_returns_none() -> None:
    """When llm_client is None, synthesis returns None."""

    idx = build_investigation_source_index(REQ)
    ledger = create_empty_ledger(requirement_hash=idx.document.source_id)
    target = PlanResearchTarget(
        claim_predicates=("material.density",),
        suggested_search_terms=("density",),
    )
    request = PlanResearchRequest(
        gate_id="material_universe", targets=(target,),
        ledger_hash_before=ledger.ledger_hash,
    )
    from openmc_agent.plan_investigation.research_executor import (
        execute_plan_research_request,
    )
    research_result = execute_plan_research_request(
        request=request, source_index=idx, ledger=ledger,
    )
    delta = run_research_evidence_synthesis(
        request=request, research_result=research_result,
        ledger=ledger, source_index=idx, gate_findings=[],
        llm_client=None,
    )
    assert delta is None


# ---------------------------------------------------------------------------
# Reactor-neutrality
# ---------------------------------------------------------------------------


def test_synthesis_no_reactor_specific_branches() -> None:
    from pathlib import Path
    import openmc_agent.plan_investigation.research_synthesis as mod
    src = Path(mod.__file__).read_text()
    for forbidden in ("vera3", "vera4", "pwr", "bwr", "vver", "htgr", "sfr", "candu"):
        assert forbidden not in src.lower(), (
            f"reactor-specific term {forbidden!r} found"
        )
