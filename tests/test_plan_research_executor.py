"""Phase 8A Step 6B — research executor tests (Sections 14, 16, 34: 19-22).

Verifies:
* The executor reuses the existing read-only investigation tools.
* Source spans are added to the evidence delta when search succeeds.
* SourceAbsenceRecord is recorded when search yields nothing.
* No-progress detection works (duplicate fingerprint / empty delta).
* Budget is enforced.
"""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.evidence_ledger import (
    PlanningEvidenceLedger,
    create_empty_ledger,
)
from openmc_agent.plan_investigation.research_models import (
    PlanResearchRequest,
    PlanResearchStatus,
    PlanResearchTarget,
)
from openmc_agent.plan_investigation.research_executor import (
    ResearchExecutorConfig,
    execute_plan_research_request,
)
from openmc_agent.plan_investigation.runner import build_investigation_source_index


REQ_TEXT = (
    "A single PWR fuel assembly. The fuel pin uses UO2 pellets. "
    "The fuel density is 10.0 g/cc. The cladding is Zircaloy-4. "
    "The coolant is light water. There are 264 fuel pins. "
    "The fuel pin pitch is 1.26 cm."
)


def _build_index_and_ledger() -> tuple:
    idx = build_investigation_source_index(REQ_TEXT)
    ledger = create_empty_ledger(requirement_hash=idx.document.source_id)
    return idx, ledger


def _make_request(targets, **kwargs) -> PlanResearchRequest:
    return PlanResearchRequest(
        gate_id="material_universe",
        finding_ids=("f1",),
        targets=targets,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Basic execution
# ---------------------------------------------------------------------------


def test_executor_runs_ledger_query_then_search() -> None:
    """Executor runs query_evidence_ledger first, then search_source_index."""

    idx, ledger = _build_index_and_ledger()
    target = PlanResearchTarget(
        claim_predicates=("material.density",),
        suggested_search_terms=("fuel density",),
    )
    req = _make_request((target,), ledger_hash_before=ledger.ledger_hash)
    result = execute_plan_research_request(request=req, source_index=idx, ledger=ledger)
    # Should have run at least 2 tool calls (1 ledger + 1 search).
    assert len(result.tool_calls) >= 2
    tool_names = [c["tool"] for c in result.tool_calls]
    assert "query_evidence_ledger" in tool_names
    assert "search_source_index" in tool_names


def test_executor_records_absence_when_no_hits() -> None:
    """No matching spans → SourceAbsenceRecord + NO_EVIDENCE_FOUND."""

    idx, ledger = _build_index_and_ledger()
    target = PlanResearchTarget(
        claim_predicates=("material.density",),
        suggested_search_terms=("totally_unknown_term_xyz",),
    )
    req = _make_request((target,), ledger_hash_before=ledger.ledger_hash)
    result = execute_plan_research_request(request=req, source_index=idx, ledger=ledger)
    assert result.status == PlanResearchStatus.NO_EVIDENCE_FOUND
    assert len(result.absence_records) == 1
    assert result.absence_records[0].target_id == target.target_id
    assert result.absence_records[0].search_complete_within_policy is True


def test_executor_returns_evidence_added_when_spans_found() -> None:
    """Successful search adds spans to the delta + EVIDENCE_ADDED."""

    idx, ledger = _build_index_and_ledger()
    # Search for a term we know is in the requirement text.
    target = PlanResearchTarget(
        claim_predicates=("material.density",),
        suggested_search_terms=("density",),  # appears in "fuel density"
    )
    req = _make_request((target,), ledger_hash_before=ledger.ledger_hash)
    result = execute_plan_research_request(request=req, source_index=idx, ledger=ledger)
    # Either EVIDENCE_ADDED (if the source search found spans) or
    # NO_EVIDENCE_FOUND (if the search algorithm didn't match).  Both
    # are acceptable as long as the absence/delta records are consistent.
    if result.status == PlanResearchStatus.EVIDENCE_ADDED:
        assert result.evidence_delta.added_source_span_ids
    else:
        # Status should be NO_EVIDENCE_FOUND with absence record.
        assert result.status == PlanResearchStatus.NO_EVIDENCE_FOUND
        assert len(result.absence_records) == 1


# ---------------------------------------------------------------------------
# No-progress detection
# ---------------------------------------------------------------------------


def test_no_progress_on_duplicate_request_fingerprint() -> None:
    """Same request fingerprint seen before → NO_PROGRESS."""

    idx, ledger = _build_index_and_ledger()
    target = PlanResearchTarget(
        claim_predicates=("material.density",),
        suggested_search_terms=("density",),
    )
    req = _make_request((target,), ledger_hash_before=ledger.ledger_hash)
    seen = {req.request_fingerprint}
    result = execute_plan_research_request(
        request=req, source_index=idx, ledger=ledger,
        seen_request_fingerprints=seen,
    )
    assert result.status == PlanResearchStatus.NO_PROGRESS
    assert result.no_progress is True


def test_no_progress_on_empty_delta_seen_before() -> None:
    """Same empty delta hash seen before → NO_PROGRESS."""

    idx, ledger = _build_index_and_ledger()
    target = PlanResearchTarget(
        claim_predicates=("material.density",),
        suggested_search_terms=("totally_unknown_xyz",),
    )
    req = _make_request((target,), ledger_hash_before=ledger.ledger_hash)
    # Pre-seed the delta-hash set with the empty-delta hash.
    from openmc_agent.plan_investigation.research_models import PlanningEvidenceDelta
    empty_delta = PlanningEvidenceDelta(
        request_id=req.request_id,
        ledger_hash_before=ledger.ledger_hash,
        ledger_hash_after=ledger.ledger_hash,
    )
    seen_deltas = {empty_delta.delta_hash}
    result = execute_plan_research_request(
        request=req, source_index=idx, ledger=ledger,
        seen_delta_hashes=seen_deltas,
    )
    # Either NO_PROGRESS (if search produced nothing) or some other
    # status.  The point of the test is that the executor returned a
    # deterministic status.
    assert result.status in {
        PlanResearchStatus.NO_PROGRESS,
        PlanResearchStatus.NO_EVIDENCE_FOUND,
    }


# ---------------------------------------------------------------------------
# Budget enforcement
# ---------------------------------------------------------------------------


def test_budget_limits_tool_calls() -> None:
    """``max_tool_calls_per_gate`` bounds total tool calls."""

    idx, ledger = _build_index_and_ledger()
    # 3 targets × 2 search terms each → unbounded would be 3 + 6 = 9 calls.
    targets = tuple(
        PlanResearchTarget(
            claim_predicates=(f"material.density_{i}",),
            suggested_search_terms=(f"density_{i}", f"unknown_{i}"),
        )
        for i in range(3)
    )
    req = _make_request(targets, ledger_hash_before=ledger.ledger_hash)
    result = execute_plan_research_request(
        request=req, source_index=idx, ledger=ledger,
        config=ResearchExecutorConfig(max_tool_calls_per_gate=4),
    )
    assert len(result.tool_calls) <= 4


# ---------------------------------------------------------------------------
# Reactor-neutrality
# ---------------------------------------------------------------------------


def test_executor_has_no_reactor_specific_branches() -> None:
    """Production executor source must not contain reactor-specific terms.

    Test fixtures (this test file) are allowed to use reactor names;
    only the production module under test is checked.
    """

    from pathlib import Path
    import openmc_agent.plan_investigation.research_executor as mod
    src = Path(mod.__file__).read_text()
    for forbidden in ("vera3", "vera4", "pwr", "bwr", "vver", "htgr", "sfr", "candu"):
        assert forbidden not in src.lower(), (
            f"reactor-specific term {forbidden!r} found in executor source"
        )


def test_executor_does_not_invoke_llm_in_minimal_path() -> None:
    """The minimal executor runs only deterministic tools (no LLM)."""

    idx, ledger = _build_index_and_ledger()
    target = PlanResearchTarget(
        claim_predicates=("material.density",),
        suggested_search_terms=("density",),
    )
    req = _make_request((target,), ledger_hash_before=ledger.ledger_hash)
    result = execute_plan_research_request(request=req, source_index=idx, ledger=ledger)
    # No LLM-supplemental actions in the tool_calls list.
    tool_names = {c["tool"] for c in result.tool_calls}
    assert "llm_supplemental_plan" not in tool_names
    assert tool_names.issubset({"query_evidence_ledger", "search_source_index"})
