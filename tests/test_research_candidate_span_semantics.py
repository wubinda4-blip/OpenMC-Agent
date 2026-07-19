"""Phase 8A Step 7 — research candidate span + evidence_added semantics.

Verifies Section 4 status semantics: finding spans != committing evidence.
"""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.evidence_ledger import create_empty_ledger
from openmc_agent.plan_investigation.research_models import (
    PlanResearchRequest, PlanResearchStatus, PlanResearchTarget,
)
from openmc_agent.plan_investigation.research_executor import (
    execute_plan_research_request, ResearchExecutorConfig,
)
from openmc_agent.plan_investigation.runner import build_investigation_source_index


REQ = "Fuel density is 10.0 g/cc. Cladding is Zircaloy-4."


def _idx_ledger():
    idx = build_investigation_source_index(REQ)
    ledger = create_empty_ledger(requirement_hash=idx.document.source_id)
    return idx, ledger


def test_candidate_spans_found_is_not_evidence_added() -> None:
    """Span search succeeds but no claim committed → CANDIDATE_SPANS_FOUND."""

    idx, ledger = _idx_ledger()
    target = PlanResearchTarget(
        claim_predicates=("material.density",),
        suggested_search_terms=("density",),
    )
    req = PlanResearchRequest(
        gate_id="material_universe",
        finding_ids=("f1",),
        targets=(target,),
        ledger_hash_before=ledger.ledger_hash,
    )
    result = execute_plan_research_request(
        request=req, source_index=idx, ledger=ledger,
    )
    # The minimal executor never commits claims; status must NOT be
    # evidence_added.
    assert result.status != PlanResearchStatus.EVIDENCE_ADDED
    # If spans were found, status is CANDIDATE_SPANS_FOUND; otherwise
    # NO_EVIDENCE_FOUND.
    assert result.status in {
        PlanResearchStatus.CANDIDATE_SPANS_FOUND,
        PlanResearchStatus.NO_EVIDENCE_FOUND,
    }


def test_evidence_added_requires_ledger_hash_change() -> None:
    """evidence_added status REQUIRES ledger_hash_after != _before.

    The minimal executor never produces evidence_added (it's reserved
    for the LLM synthesis path).  We verify the invariant by checking
    that no executor result with EVIDENCE_ADDED has matching hashes.
    """

    idx, ledger = _idx_ledger()
    target = PlanResearchTarget(
        claim_predicates=("material.density",),
        suggested_search_terms=("density",),
    )
    req = PlanResearchRequest(
        gate_id="material_universe", targets=(target,),
        ledger_hash_before=ledger.ledger_hash,
    )
    result = execute_plan_research_request(request=req, source_index=idx, ledger=ledger)
    if result.status == PlanResearchStatus.EVIDENCE_ADDED:
        assert result.ledger_hash_after != result.evidence_delta.ledger_hash_before
    else:
        # Minimal executor: ledger hash unchanged.
        assert result.ledger_hash_after == ledger.ledger_hash


def test_no_progress_on_duplicate_request_fingerprint() -> None:
    """Same request fingerprint → NO_PROGRESS."""

    idx, ledger = _idx_ledger()
    target = PlanResearchTarget(
        claim_predicates=("material.density",),
        suggested_search_terms=("density",),
    )
    req = PlanResearchRequest(
        gate_id="material_universe", targets=(target,),
        ledger_hash_before=ledger.ledger_hash,
    )
    # First call.
    execute_plan_research_request(request=req, source_index=idx, ledger=ledger)
    # Second call with same fingerprint.
    result = execute_plan_research_request(
        request=req, source_index=idx, ledger=ledger,
        seen_request_fingerprints={req.request_fingerprint},
    )
    assert result.status == PlanResearchStatus.NO_PROGRESS
    assert result.no_progress is True
