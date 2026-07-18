"""Tests for Axial Geometry critic normalization (detailed rejection paths)."""

from openmc_agent.plan_builder.closed_loop.axial_geometry_reviewer import _normalize
from openmc_agent.plan_builder.closed_loop.models import (
    AxialGeometryReviewFindingDraft,
    AxialGeometryReviewModelOutput,
    PlanFindingCategory,
    PlanFindingSeverity,
)


class _FakeEvidenceItem:
    def __init__(self, ref_id):
        self.ref_id = ref_id
        self.canonical_hash = "h"
        self.value = {"v": True}


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
        self.evidence_items = [_FakeEvidenceItem("F001")]
        self.contract_matrix = type("M", (), {"rows": [_FakeRow("sdc:x")]})()
        self.binding_view = _FakeBinding()


def test_blank_code_rejected():
    pack = _FakePack()
    output = AxialGeometryReviewModelOutput(
        review_status="complete",
        findings=[AxialGeometryReviewFindingDraft(code="  ")],
    )
    _, rejected = _normalize(output, pack)
    assert any(r["code"] == "axial_geometry_review.invalid_finding_contract" for r in rejected)


def test_human_and_repairable_contradiction_fixed_by_model():
    """The model validator auto-sets repairable_by_llm=False when requires_human=True."""
    pack = _FakePack()
    draft = AxialGeometryReviewFindingDraft(code="axial.test", evidence_refs=["F001"], requires_human=True, repairable_by_llm=True)
    assert draft.requires_human is True
    assert draft.repairable_by_llm is False  # auto-fixed by model validator


def test_error_without_evidence_rejected():
    pack = _FakePack()
    output = AxialGeometryReviewModelOutput(
        review_status="complete",
        findings=[AxialGeometryReviewFindingDraft(code="axial.test", severity=PlanFindingSeverity.ERROR, evidence_refs=[])],
    )
    _, rejected = _normalize(output, pack)
    assert any(r["code"] == "axial_geometry_review.invalid_finding_contract" for r in rejected)


def test_openmc_runtime_claim_rejected():
    pack = _FakePack()
    output = AxialGeometryReviewModelOutput(
        review_status="complete",
        findings=[AxialGeometryReviewFindingDraft(code="axial.test", evidence_refs=["F001"], metadata={"openmc_runtime": "ok"})],
    )
    _, rejected = _normalize(output, pack)
    assert any(r["code"] == "axial_geometry_review.root_reachability_forbidden" for r in rejected)


def test_keff_claim_rejected():
    pack = _FakePack()
    output = AxialGeometryReviewModelOutput(
        review_status="complete",
        findings=[AxialGeometryReviewFindingDraft(code="axial.test", evidence_refs=["F001"], metadata={"keff": 1.0})],
    )
    _, rejected = _normalize(output, pack)
    assert any(r["code"] == "axial_geometry_review.root_reachability_forbidden" for r in rejected)
