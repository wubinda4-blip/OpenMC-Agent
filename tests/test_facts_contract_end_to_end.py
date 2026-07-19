"""Phase 8C Step 2 — End-to-end contract enforcement simulation.

Verifies the central deliverable of Phase 8C Step 0-2: when the
investigation ledger carries source-backed multi-assembly evidence,
the skeleton mining + merge + preflight chain deterministically
forces the candidate to ``multi_assembly_core``, regardless of what
the LLM emits.

This is a reactor-neutral test: no VERA4 string anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from openmc_agent.plan_builder.facts_requirement_skeleton import (
    compile_facts_requirement_skeleton,
    FactsRequirementSkeleton,
)
from openmc_agent.plan_builder.facts_evidence_contract import (
    compile_facts_evidence_contract,
    merge_facts_content_into_skeleton,
    run_facts_skeleton_preflight,
    FactsContentProposal,
)
from openmc_agent.plan_investigation.models import (
    EvidenceClaim,
    EvidenceCriticality,
    EvidenceSourceRef,
    EvidenceStatus,
)


@dataclass
class _FakeFeatureContract:
    contract_hash: str = "fc-multi"
    multi_assembly_core: bool = True
    has_spacer_grid: bool = True
    has_axial_geometry: bool = True
    has_special_pin_map: bool = True
    has_localized_insert: bool = True
    has_multiple_fuel_variants: bool = True
    has_control_state: bool = True
    has_multi_segment_localized_insert: bool = True
    evidence: dict[str, Any] = None

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = {"feature_summary": {}}


@dataclass
class _FakeLedger:
    claims: list = None
    conflicts: dict = None
    ledger_hash: str = "ld-multi"

    def __post_init__(self):
        if self.claims is None:
            self.claims = []
        if self.conflicts is None:
            self.conflicts = {}


def _claim(*, subject: str, value: Any) -> EvidenceClaim:
    return EvidenceClaim(
        claim_id="",
        subject=subject,
        predicate="is",
        value=value,
        status=EvidenceStatus.EXPLICIT,
        criticality=EvidenceCriticality.INFORMATIONAL,
        source_refs=(
            EvidenceSourceRef(source_id="s1", span_id="sp1", excerpt_hash="eh1"),
        ),
        confidence=0.9,
    )


def _build_multi_assembly_ledger() -> _FakeLedger:
    """A reactor-neutral source-backed multi-assembly evidence set."""
    return _FakeLedger(claims=[
        _claim(subject="model_scope", value="multi_assembly_core"),
        _claim(subject="core_lattice_size", value=[3, 3]),
        _claim(subject="assembly_count", value=9),
        _claim(subject="assembly_type_counts", value={"A": 4, "B": 4, "C": 1}),
        _claim(subject="has_spacer_grids", value=True),
        _claim(subject="has_axial_geometry", value=True),
        _claim(subject="has_special_pin_map", value=True),
        _claim(subject="fuel_variant", value={
            "variant_id": "region1", "enrichment_wt_percent": 2.0,
            "density_g_cm3": 10.0, "assembly_type_ids": ["A"],
        }),
        _claim(subject="fuel_variant", value={
            "variant_id": "region2", "enrichment_wt_percent": 3.0,
            "density_g_cm3": 10.0, "assembly_type_ids": ["B"],
        }),
        _claim(subject="localized_insert", value={
            "requirement_id": "absorber_a",
            "insert_kind": "control_rod",
            "assembly_type_ids": ["C"],
            "expected_coordinate_count_per_assembly": 24,
        }),
    ])


def test_end_to_end_contract_forces_multi_assembly_on_wrong_llm_output():
    """When the LLM emits ``single_assembly`` but the contract says
    multi-assembly, the merge restores ``multi_assembly_core`` and the
    preflight flags the LLM's original choice.
    """
    ledger = _build_multi_assembly_ledger()
    skel_result = compile_facts_requirement_skeleton(
        requirement_text="reactor-neutral benchmark",
        feature_contract=_FakeFeatureContract(),
        evidence_ledger=ledger,
    )
    assert skel_result.ok
    skeleton = skel_result.skeleton
    # The skeleton itself is correctly locked.
    assert skeleton.model_scope.value == "multi_assembly_core"
    assert skeleton.model_scope.status == "source_backed"
    assert skeleton.assembly_layout.assembly_count == 9
    assert len(skeleton.fuel_variant_slots) == 2
    assert len(skeleton.localized_insert_slots) == 1

    # The LLM emits the wrong scope.
    bad_proposal = FactsContentProposal(
        proposal_id="bad",
        resolved_fields={
            "model_scope": "single_assembly",
            "assembly_count": 1,
            "has_spacer_grids": False,
        },
    )
    merge_result = merge_facts_content_into_skeleton(skeleton, bad_proposal)
    assert merge_result.ok
    candidate = merge_result.merged.patch
    # Merge restored the locked values.
    assert candidate["model_scope"] == "multi_assembly_core"
    assert candidate["assembly_count"] == 9
    assert candidate["has_spacer_grids"] is True
    # Fuel variants are preserved.
    fv_ids = {v["variant_id"] for v in candidate["fuel_variant_requirements"]}
    assert fv_ids == {"region1", "region2"}
    # Localized insert preserved.
    li_ids = {i["requirement_id"] for i in candidate["localized_insert_requirements"]}
    assert li_ids == {"absorber_a"}

    # Preflight on the merged candidate must pass (no violations).
    preflight = run_facts_skeleton_preflight(skeleton, candidate)
    assert preflight.ok, f"Preflight failed: {preflight.issues}"


def test_end_to_end_preflight_flags_attempted_locked_value_override():
    """When the candidate modifies a locked slot, the preflight emits
    ``facts_contract.locked_field_modified`` and the merge cannot
    silently re-accept the new value.
    """
    ledger = _build_multi_assembly_ledger()
    skel_result = compile_facts_requirement_skeleton(
        requirement_text="reactor-neutral benchmark",
        feature_contract=_FakeFeatureContract(),
        evidence_ledger=ledger,
    )
    skeleton = skel_result.skeleton

    # Simulate a candidate that was constructed bypassing the merge
    # (e.g. the LLM emitted the wrong value and the merge was not run).
    bad_candidate = {
        "model_scope": "single_assembly",
        "assembly_count": 1,
        "has_spacer_grids": False,
        "fuel_variant_requirements": [],
        "localized_insert_requirements": [],
    }
    preflight = run_facts_skeleton_preflight(skeleton, bad_candidate)
    assert not preflight.ok
    codes = [i["code"] for i in preflight.issues]
    # Locked scope modification is flagged (both legacy and new alias).
    assert "facts_skeleton.immutable_field_modified" in codes
    assert "facts_contract.locked_field_modified" in codes
    # Missing fuel variants flagged.
    assert "facts_skeleton.fuel_variant_modified" in codes
    assert "facts_contract.fuel_variant_missing" in codes
    # Missing localized inserts flagged.
    assert "facts_contract.localized_insert_missing" in codes


def test_end_to_end_conflict_scope_marked_not_silently_resolved():
    """When source claims disagree on the scope, the skeleton marks
    ``conflict`` and the merge cannot pick one silently.
    """
    ledger = _FakeLedger(claims=[
        _claim(subject="model_scope", value="multi_assembly_core"),
        _claim(subject="model_scope", value="single_assembly"),
    ])
    skel_result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(multi_assembly_core=False),
        evidence_ledger=ledger,
    )
    skeleton = skel_result.skeleton
    assert skeleton.model_scope.status == "conflict"
    assert "scope" in skeleton.conflicting_slots

    # The LLM proposes a value; the merge uses it but emits a warning.
    proposal = FactsContentProposal(resolved_fields={"model_scope": "multi_assembly_core"})
    merge_result = merge_facts_content_into_skeleton(skeleton, proposal)
    assert merge_result.ok
    assert any("conflict" in w for w in merge_result.warnings)


def test_end_to_end_deterministic_derivation_locks_against_wrong_llm_value():
    """When the feature contract says multi-assembly but no claim exists,
    the skeleton deterministically derives multi_assembly_core, and the
    merge rejects an LLM attempt to set single_assembly.
    """
    ledger = _FakeLedger(claims=[])
    skel_result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(),
        evidence_ledger=ledger,
    )
    skeleton = skel_result.skeleton
    assert skeleton.model_scope.value == "multi_assembly_core"
    assert skeleton.model_scope.status == "deterministically_derived"

    bad_proposal = FactsContentProposal(
        resolved_fields={"model_scope": "single_assembly"},
    )
    merge_result = merge_facts_content_into_skeleton(skeleton, bad_proposal)
    candidate = merge_result.merged.patch
    assert candidate["model_scope"] == "multi_assembly_core", (
        "Deterministically-derived multi_assembly_core must override the "
        "LLM's single_assembly proposal."
    )
