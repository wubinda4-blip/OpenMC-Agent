"""Tests for Axial Geometry critic normalization and rejection."""

from openmc_agent.plan_builder.closed_loop.axial_geometry_reviewer import _normalize, AxialGeometryReviewResult
from openmc_agent.plan_builder.closed_loop.models import (
    AxialGeometryReviewFindingDraft,
    AxialGeometryReviewModelOutput,
    AxialGeometryReviewCoverageSummary,
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


class _FakeBinding:
    def __init__(self):
        self.axial_layer_records = []
        self.axial_overlay_records = []
        self.lattice_loading_records = []
        self.base_path_profile_records = []
        self.localized_insert_axial_records = []


class _FakePack:
    def __init__(self):
        self.evidence_items = [_FakeEvidenceItem("F001"), _FakeEvidenceItem("A001"), _FakeEvidenceItem("O001")]
        self.contract_matrix = type("M", (), {"rows": [_FakeRow("sdc:x"), _FakeRow("lfb:l1"), _FakeRow("ovb:o1")]})()
        self.binding_view = _FakeBinding()


def test_unknown_evidence_ref_rejected():
    pack = _FakePack()
    output = AxialGeometryReviewModelOutput(
        review_status="complete",
        findings=[AxialGeometryReviewFindingDraft(code="axial.test", evidence_refs=["ZZZ999"])],
    )
    findings, rejected = _normalize(output, pack)
    assert len(findings) == 0
    assert any(r["code"] == "axial_geometry_review.unknown_evidence_ref" for r in rejected)


def test_unknown_contract_row_rejected():
    pack = _FakePack()
    output = AxialGeometryReviewModelOutput(
        review_status="complete",
        findings=[AxialGeometryReviewFindingDraft(code="axial.test", evidence_refs=["F001"], contract_row_ids=["unknown_row"])],
    )
    findings, rejected = _normalize(output, pack)
    assert len(findings) == 0
    assert any(r["code"] == "axial_geometry_review.unknown_contract_row" for r in rejected)


def test_owner_action_field_rejected():
    pack = _FakePack()
    output = AxialGeometryReviewModelOutput(
        review_status="complete",
        findings=[AxialGeometryReviewFindingDraft(code="axial.test", evidence_refs=["F001"], metadata={"owner": "materials"})],
    )
    findings, rejected = _normalize(output, pack)
    assert len(findings) == 0
    assert any(r["code"] == "axial_geometry_review.owner_action_forbidden" for r in rejected)


def test_root_reachability_claim_rejected():
    pack = _FakePack()
    output = AxialGeometryReviewModelOutput(
        review_status="complete",
        findings=[AxialGeometryReviewFindingDraft(code="axial.test", evidence_refs=["F001"], metadata={"root_reachable": True})],
    )
    findings, rejected = _normalize(output, pack)
    assert len(findings) == 0
    assert any(r["code"] == "axial_geometry_review.root_reachability_forbidden" for r in rejected)


def test_valid_finding_accepted():
    pack = _FakePack()
    output = AxialGeometryReviewModelOutput(
        review_status="complete",
        findings=[AxialGeometryReviewFindingDraft(
            code="axial.warning_test", severity=PlanFindingSeverity.WARNING,
            evidence_refs=["F001"], message="test finding",
            contract_row_ids=["sdc:x"],
        )],
    )
    findings, rejected = _normalize(output, pack)
    assert len(findings) == 1
    assert len(rejected) == 0
    assert findings[0].code == "axial.warning_test"
