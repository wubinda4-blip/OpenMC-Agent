"""Tests for Assembled Plan critic normalization."""

from openmc_agent.plan_builder.closed_loop.assembled_plan_reviewer import _normalize
from openmc_agent.plan_builder.closed_loop.models import (
    AssembledPlanReviewFindingDraft,
    AssembledPlanReviewModelOutput,
    PlanFindingCategory,
    PlanFindingSeverity,
)


class _FakeEvidenceItem:
    def __init__(self, ref_id):
        self.ref_id = ref_id
        self.canonical_hash = ""
        self.value = {"test": True}


class _FakeRow:
    def __init__(self, row_id):
        self.row_id = row_id


class _FakePack:
    def __init__(self):
        self.evidence_items = [_FakeEvidenceItem("G001"), _FakeEvidenceItem("R001")]
        self.contract_matrix = type("M", (), {"rows": [_FakeRow("rs:root"), _FakeRow("rr:reachability")]})()
        self.binding_view = None


def test_unknown_evidence_ref_rejected():
    pack = _FakePack()
    output = AssembledPlanReviewModelOutput(
        review_status="complete",
        findings=[AssembledPlanReviewFindingDraft(code="assembled.test", evidence_refs=["ZZZ999"])],
    )
    findings, rejected = _normalize(output, pack)
    assert len(findings) == 0
    assert any(r["code"] == "assembled_plan_review.unknown_evidence_ref" for r in rejected)


def test_owner_action_rejected():
    pack = _FakePack()
    output = AssembledPlanReviewModelOutput(
        review_status="complete",
        findings=[AssembledPlanReviewFindingDraft(code="assembled.test", evidence_refs=["G001"], metadata={"owner": "facts"})],
    )
    findings, rejected = _normalize(output, pack)
    assert len(findings) == 0
    assert any(r["code"] == "assembled_plan_review.owner_action_forbidden" for r in rejected)


def test_runtime_claim_rejected():
    pack = _FakePack()
    output = AssembledPlanReviewModelOutput(
        review_status="complete",
        findings=[AssembledPlanReviewFindingDraft(code="assembled.test", evidence_refs=["G001"], metadata={"keff": 1.0})],
    )
    findings, rejected = _normalize(output, pack)
    assert len(findings) == 0
    assert any(r["code"] == "assembled_plan_review.runtime_claim_forbidden" for r in rejected)


def test_valid_finding_accepted():
    pack = _FakePack()
    output = AssembledPlanReviewModelOutput(
        review_status="complete",
        findings=[AssembledPlanReviewFindingDraft(
            code="assembled.warning_test", severity=PlanFindingSeverity.WARNING,
            evidence_refs=["G001"], message="test finding",
            contract_row_ids=["rs:root"],
        )],
    )
    findings, rejected = _normalize(output, pack)
    assert len(findings) == 1
    assert len(rejected) == 0
