"""Phase 8A Step 6B — research models tests (Section 34: 18-21).

Verifies the typed research models enforce their invariants:

* Targets always have a deterministic hash + id.
* Requests fingerprint includes gate_id + finding_ids + targets.
* PlanningEvidenceDelta detects no-progress (empty delta).
* SourceAbsenceRecord hash is stable.
* PlanResearchResult rejects unknown status values.
"""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.research_models import (
    PlanResearchRequest,
    PlanResearchResult,
    PlanResearchStatus,
    PlanResearchTarget,
    PlanningEvidenceDelta,
    SourceAbsenceRecord,
    RESEARCH_SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Target
# ---------------------------------------------------------------------------


def test_target_has_deterministic_hash_and_id() -> None:
    t = PlanResearchTarget(
        claim_predicates=("material.density",),
        suggested_search_terms=("fuel density",),
    )
    assert t.target_hash
    assert t.target_id.startswith("target_")


def test_target_hash_stable_for_same_inputs() -> None:
    """Two targets with the same body have the same hash."""

    t1 = PlanResearchTarget(
        claim_predicates=("material.density",),
        target_component_ids=("fuel_pin",),
    )
    t2 = PlanResearchTarget(
        claim_predicates=("material.density",),
        target_component_ids=("fuel_pin",),
    )
    assert t1.target_hash == t2.target_hash


def test_target_hash_changes_when_predicate_changes() -> None:
    t1 = PlanResearchTarget(claim_predicates=("material.density",))
    t2 = PlanResearchTarget(claim_predicates=("material.role_required",))
    assert t1.target_hash != t2.target_hash


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


def test_request_has_stable_fingerprint() -> None:
    """Request fingerprint is deterministic for same inputs."""

    r1 = PlanResearchRequest(
        gate_id="material_universe",
        finding_ids=("f1", "f2"),
        issue_codes=("inventory.material_role_uncovered",),
        targets=(PlanResearchTarget(claim_predicates=("material.density",)),),
        ledger_hash_before="lh1",
    )
    r2 = PlanResearchRequest(
        gate_id="material_universe",
        finding_ids=("f1", "f2"),
        issue_codes=("inventory.material_role_uncovered",),
        targets=(PlanResearchTarget(claim_predicates=("material.density",)),),
        ledger_hash_before="lh1",
    )
    assert r1.request_fingerprint == r2.request_fingerprint
    assert r1.request_id.startswith("research_")


def test_request_fingerprint_changes_when_ledger_changes() -> None:
    """Fingerprint includes ledger_hash_before so a research request
    after new evidence was added is NOT considered a duplicate."""

    r1 = PlanResearchRequest(
        gate_id="material_universe",
        finding_ids=("f1",),
        ledger_hash_before="lh1",
    )
    r2 = PlanResearchRequest(
        gate_id="material_universe",
        finding_ids=("f1",),
        ledger_hash_before="lh2",
    )
    assert r1.request_fingerprint != r2.request_fingerprint


# ---------------------------------------------------------------------------
# EvidenceDelta
# ---------------------------------------------------------------------------


def test_delta_detectsNoProgressWhenEmpty() -> None:
    """Empty delta (no new claims) is flagged as no-progress."""

    delta = PlanningEvidenceDelta(
        request_id="r1",
        ledger_hash_before="lh1",
        ledger_hash_after="lh1",  # same hash
    )
    assert delta.is_empty is True


def test_delta_not_empty_when_claims_added() -> None:
    delta = PlanningEvidenceDelta(
        request_id="r1",
        ledger_hash_before="lh1",
        ledger_hash_after="lh2",
        added_claim_ids=("claim_1", "claim_2"),
    )
    assert delta.is_empty is False
    assert delta.delta_id.startswith("delta_")


def test_delta_hash_stable() -> None:
    d1 = PlanningEvidenceDelta(
        request_id="r", ledger_hash_before="a", ledger_hash_after="b",
        added_claim_ids=("c1",),
    )
    d2 = PlanningEvidenceDelta(
        request_id="r", ledger_hash_before="a", ledger_hash_after="b",
        added_claim_ids=("c1",),
    )
    assert d1.delta_hash == d2.delta_hash


# ---------------------------------------------------------------------------
# SourceAbsenceRecord
# ---------------------------------------------------------------------------


def test_source_absence_has_stable_hash() -> None:
    a1 = SourceAbsenceRecord(
        request_id="r", target_id="t",
        source_ids_searched=("doc1",),
        query_fingerprints=("q1",),
        search_result_counts={"doc1": 0},
        search_complete_within_policy=True,
    )
    a2 = SourceAbsenceRecord(
        request_id="r", target_id="t",
        source_ids_searched=("doc1",),
        query_fingerprints=("q1",),
        search_result_counts={"doc1": 0},
        search_complete_within_policy=True,
    )
    assert a1.absence_hash == a2.absence_hash
    assert a1.absence_id.startswith("absence_")


# ---------------------------------------------------------------------------
# PlanResearchResult
# ---------------------------------------------------------------------------


def test_result_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="unknown research status"):
        PlanResearchResult(
            request_id="r",
            status="totally_invalid_status",
        )


def test_result_evidence_added_carries_delta() -> None:
    delta = PlanningEvidenceDelta(
        request_id="r", ledger_hash_before="a", ledger_hash_after="b",
        added_claim_ids=("c1",),
    )
    result = PlanResearchResult(
        request_id="r",
        status=PlanResearchStatus.EVIDENCE_ADDED,
        evidence_delta=delta,
        ledger_hash_after="b",
        resolved_finding_ids=("f1",),
    )
    assert result.status == "evidence_added"
    assert result.evidence_delta is not None
    assert result.result_hash


def test_result_no_progress_status() -> None:
    result = PlanResearchResult(
        request_id="r",
        status=PlanResearchStatus.NO_PROGRESS,
        no_progress=True,
    )
    assert result.no_progress is True


def test_result_requires_human_status() -> None:
    result = PlanResearchResult(
        request_id="r",
        status=PlanResearchStatus.REQUIRES_HUMAN,
        requires_human_finding_ids=("f1",),
    )
    assert result.requires_human_finding_ids == ("f1",)


def test_schema_version_is_present() -> None:
    assert RESEARCH_SCHEMA_VERSION == "1.0"
