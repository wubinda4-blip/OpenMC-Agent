"""Tests for component evidence source validation + ledger binding."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.component_evidence import (
    ComponentEvidenceProposal,
    ComponentEvidenceSynthesisResult,
)
from openmc_agent.plan_investigation.evidence_ledger import (
    create_empty_ledger,
    find_claims,
)
from openmc_agent.plan_investigation.evidence_synthesis import (
    bind_synthesis_result_to_ledger,
    validate_component_evidence_proposal,
)
from openmc_agent.plan_investigation.models import SourceKind
from openmc_agent.plan_investigation.source_index import build_source_index


def _index(text="Fuel enrichment is 3.5 wt%.\nCoolant density 0.99 g/cm3.\n"):
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    # Register the spans the proposals will reference.
    span1 = idx.make_span(1, 1)
    span2 = idx.make_span(2, 2)
    idx.register_span(span1)
    idx.register_span(span2)
    return idx, span1, span2


def _ledger(idx):
    return create_empty_ledger(requirement_hash="rh", source_indexes=[idx])


def test_valid_proposal_accepted_to_ledger() -> None:
    idx, span1, _ = _index()
    ld = _ledger(idx)
    proposal = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        subject="fuel_enrichment",
        predicate="material.composition_present",
        value={"enrichment_wt_percent": 3.5},
        source_span_ids=(span1.span_id,),
    )
    outcome = validate_component_evidence_proposal(
        proposal, source_indexes={idx.document.source_id: idx}, ledger=ld
    )
    assert outcome.accepted


def test_unknown_span_id_rejected() -> None:
    idx, _, _ = _index()
    ld = _ledger(idx)
    proposal = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        subject="x",
        predicate="material.composition_present",
        value=3.5,
        source_span_ids=("span_foreign",),
    )
    outcome = validate_component_evidence_proposal(
        proposal, source_indexes={idx.document.source_id: idx}, ledger=ld
    )
    assert not outcome.accepted
    assert "source_span_unknown" in outcome.reason_code


def test_value_not_in_source_excerpt_rejected() -> None:
    """A numerical value that doesn't appear in the referenced span
    excerpt must be rejected (no fabrication)."""
    idx, span1, _ = _index()
    ld = _ledger(idx)
    proposal = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        subject="enrichment",
        predicate="material.composition_present",
        value=99.9,  # not in source
        source_span_ids=(span1.span_id,),
    )
    outcome = validate_component_evidence_proposal(
        proposal, source_indexes={idx.document.source_id: idx}, ledger=ld
    )
    assert not outcome.accepted
    assert "value_not_source_backed" in outcome.reason_code


def test_value_in_source_accepted() -> None:
    idx, span1, _ = _index()
    ld = _ledger(idx)
    proposal = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        subject="enrichment",
        predicate="material.composition_present",
        value=3.5,  # in source excerpt "3.5 wt%"
        source_span_ids=(span1.span_id,),
    )
    outcome = validate_component_evidence_proposal(
        proposal, source_indexes={idx.document.source_id: idx}, ledger=ld
    )
    assert outcome.accepted


def test_conflicting_value_at_same_semantic_key_rejected() -> None:
    """Two proposals with the same semantic key but different value:
    second is rejected as a conflict.  Both values must be present in
    source so the value-source check doesn't fire first.
    """
    text = "Section A: enrichment is 3.5 wt%.\nSection B: enrichment is 4.5 wt%.\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    span1 = idx.make_span(1, 1)
    span2 = idx.make_span(2, 2)
    idx.register_span(span1)
    idx.register_span(span2)
    ld = _ledger(idx)
    first = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        subject="enrichment",
        predicate="material.composition_present",
        value=3.5,
        source_span_ids=(span1.span_id,),
    )
    second = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        subject="enrichment",
        predicate="material.composition_present",
        value=4.5,
        source_span_ids=(span2.span_id,),
    )
    sources = {idx.document.source_id: idx}
    out1 = validate_component_evidence_proposal(first, source_indexes=sources, ledger=ld)
    assert out1.accepted
    # Accept the first one so it lands in the ledger.
    from openmc_agent.plan_investigation.evidence_synthesis import accept_component_evidence_proposal
    accept_component_evidence_proposal(
        first, source_indexes=sources, ledger=ld, patch_type="materials"
    )
    out2 = validate_component_evidence_proposal(second, source_indexes=sources, ledger=ld)
    assert not out2.accepted
    assert "conflict" in out2.reason_code


def test_source_backed_predicate_requires_span() -> None:
    """geometry.profile_radius_boundary requires at least one span."""
    idx, _, _ = _index()
    ld = _ledger(idx)
    proposal = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        subject="x",
        predicate="geometry.profile_radius_boundary",
        value=0.5,
        source_span_ids=(),  # no span
    )
    outcome = validate_component_evidence_proposal(
        proposal, source_indexes={idx.document.source_id: idx}, ledger=ld
    )
    assert not outcome.accepted


def test_non_value_predicate_does_not_require_value_in_source() -> None:
    """geometry.component_present is not value-bearing, so it accepts
    without source-token recovery."""
    idx, span1, _ = _index()
    ld = _ledger(idx)
    proposal = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="spacer_grid",
        subject="spacer_grid",
        predicate="geometry.component_present",
        value=True,
        source_span_ids=(span1.span_id,),
    )
    outcome = validate_component_evidence_proposal(
        proposal, source_indexes={idx.document.source_id: idx}, ledger=ld
    )
    assert outcome.accepted


def test_bind_synthesis_result_to_ledger_accepts_valid() -> None:
    idx, span1, span2 = _index()
    ld = _ledger(idx)
    result = ComponentEvidenceSynthesisResult(
        patch_type="materials",
        proposals=(
            ComponentEvidenceProposal(
                proposal_id="",
                component_kind="fuel_pin",
                subject="enrichment",
                predicate="material.composition_present",
                value=3.5,
                source_span_ids=(span1.span_id,),
            ),
            ComponentEvidenceProposal(
                proposal_id="",
                component_kind="moderator_region",
                subject="density",
                predicate="material.density_present",
                value=0.99,
                source_span_ids=(span2.span_id,),
            ),
        ),
    )
    sources = {idx.document.source_id: idx}
    report = bind_synthesis_result_to_ledger(
        result=result, source_indexes=sources, ledger=ld
    )
    assert len(report.accepted_claim_ids) == 2
    assert len(report.rejected_proposal_ids) == 0
    # The ledger now has the new claims.
    matches = find_claims(ld, predicate="material.composition_present")
    assert len(matches) == 1


def test_bind_mixed_valid_and_invalid() -> None:
    idx, span1, _ = _index()
    ld = _ledger(idx)
    result = ComponentEvidenceSynthesisResult(
        patch_type="materials",
        proposals=(
            ComponentEvidenceProposal(
                proposal_id="",
                component_kind="fuel_pin",
                subject="enrichment",
                predicate="material.composition_present",
                value=3.5,
                source_span_ids=(span1.span_id,),
            ),
            ComponentEvidenceProposal(
                proposal_id="",
                component_kind="fuel_pin",
                subject="bogus",
                predicate="geometry.component_present",  # non-value predicate
                value="custom_fabricated_value",
                source_span_ids=(span1.span_id,),
            ),
        ),
    )
    sources = {idx.document.source_id: idx}
    report = bind_synthesis_result_to_ledger(
        result=result, source_indexes=sources, ledger=ld
    )
    # geometry.component_present doesn't require source-token recovery,
    # so both proposals are accepted (the "fabricated_value" string has
    # no numerical tokens to verify).  The test verifies the binder
    # does not crash on mixed kinds.
    assert len(report.accepted_claim_ids) == 2


def test_accepted_claim_carries_component_metadata() -> None:
    idx, span1, _ = _index()
    ld = _ledger(idx)
    proposal = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        profile_kind="active_fuel_pin",
        subject="enrichment",
        predicate="material.composition_present",
        value=3.5,
        source_span_ids=(span1.span_id,),
        material_roles=("fuel",),
        cell_roles=("fuel",),
    )
    sources = {idx.document.source_id: idx}
    from openmc_agent.plan_investigation.evidence_synthesis import accept_component_evidence_proposal
    claim_id, claim = accept_component_evidence_proposal(
        proposal, source_indexes=sources, ledger=ld, patch_type="materials"
    )
    assert claim.metadata["component_kind"] == "fuel_pin"
    assert claim.metadata["profile_kind"] == "active_fuel_pin"
    assert claim.metadata["material_roles"] == ["fuel"]
    assert claim.required_by_patch_types == ("materials",)
