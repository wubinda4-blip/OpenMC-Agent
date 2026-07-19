"""Phase 8C Step 2 — compile_facts_requirement_skeleton now mines the
evidence ledger for slot values.

Phase 8C Step 0 audit defect: the compiler only used ``confirmed_facts``
(human-confirmed) and treated ``evidence_ledger`` purely as a hash source.
For benchmarks like VERA4 that have no human-confirmed facts, the
skeleton was empty and the contract was a no-op.

These tests verify the new reactor-neutral mining behavior:
- Source-backed scope claims lock the scope slot.
- Feature-contract fallback locks multi_assembly_core when the feature
  detector says so, even without a claim.
- Conflicting claims for the same slot mark it as ``conflict`` rather
  than silently picking one.
- Fuel variant and localized insert claims become locked slots.
- Deterministic derivation (rows × cols = assembly_count) is supported.
- Source absence is NOT fabricated as ``False``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from openmc_agent.plan_builder.facts_requirement_skeleton import (
    compile_facts_requirement_skeleton,
    FactsRequirementSkeleton,
)
from openmc_agent.plan_investigation.models import (
    EvidenceClaim,
    EvidenceCriticality,
    EvidenceSourceRef,
    EvidenceStatus,
)


# ---------------------------------------------------------------------------
# Test helpers — fakes that match the EvidenceLedger / FeatureContract
# attributes the compiler reads.  reactor-neutral (no VERA4 anywhere).
# ---------------------------------------------------------------------------


@dataclass
class _FakeFeatureContract:
    contract_hash: str = "fc-v1"
    multi_assembly_core: bool = False
    has_spacer_grid: bool = False
    has_axial_geometry: bool = False
    has_special_pin_map: bool = False
    has_localized_insert: bool = False
    has_multiple_fuel_variants: bool = False
    has_control_state: bool = False
    has_multi_segment_localized_insert: bool = False
    evidence: dict[str, Any] = None

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = {"feature_summary": {}}


@dataclass
class _FakeSourceIndex:
    index_hash: str = "si-v1"


@dataclass
class _FakeLedger:
    claims: list[EvidenceClaim] = None
    conflicts: dict[str, Any] = None
    ledger_hash: str = "ld-v1"

    def __post_init__(self):
        if self.claims is None:
            self.claims = []
        if self.conflicts is None:
            self.conflicts = {}


def _explicit(
    *, subject: str, value: Any, claim_id: str = "",
    source_span_id: str = "span1", confidence: float = 0.9,
    required_by_json_paths: tuple[str, ...] = (),
) -> EvidenceClaim:
    return EvidenceClaim(
        claim_id=claim_id,
        subject=subject,
        predicate="is",
        value=value,
        status=EvidenceStatus.EXPLICIT,
        criticality=EvidenceCriticality.INFORMATIONAL,
        source_refs=(
            EvidenceSourceRef(
                source_id="src1", span_id=source_span_id, excerpt_hash="eh1",
            ),
        ),
        confidence=confidence,
        required_by_json_paths=required_by_json_paths,
    )


def _claim_id(claim: EvidenceClaim) -> str:
    """Helper to read the auto-computed claim id."""
    return claim.claim_id


# ---------------------------------------------------------------------------
# Scope mining
# ---------------------------------------------------------------------------


def test_source_backed_scope_claim_locks_scope_slot():
    """A source-backed ``model_scope=multi_assembly_core`` claim produces
    a locked ``source_backed`` scope slot with the claim id recorded.
    """
    scope_claim = _explicit(subject="model_scope", value="multi_assembly_core")
    ledger = _FakeLedger(claims=[scope_claim])
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(multi_assembly_core=True),
        evidence_ledger=ledger,
    )
    skel = result.skeleton
    assert skel is not None
    assert skel.model_scope.value == "multi_assembly_core"
    assert skel.model_scope.status == "source_backed"
    assert skel.model_scope.source_claim_ids == [_claim_id(scope_claim)]
    assert skel.model_scope.immutable is True


def test_feature_contract_alone_can_lock_multi_assembly_scope():
    """When no claim exists but the feature detector says multi-assembly,
    the compiler deterministically derives ``multi_assembly_core``.
    """
    ledger = _FakeLedger(claims=[])
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(multi_assembly_core=True),
        evidence_ledger=ledger,
    )
    skel = result.skeleton
    assert skel.model_scope.value == "multi_assembly_core"
    assert skel.model_scope.status == "deterministically_derived"
    assert "feature_contract.multi_assembly_core" in skel.model_scope.derivation_codes


def test_conflicting_scope_claims_mark_slot_as_conflict():
    """Two source-backed claims with different scope values must not
    silently pick one — the slot becomes ``conflict`` so the gate asks
    a human.
    """
    ledger = _FakeLedger(claims=[
        _explicit(subject="model_scope", value="multi_assembly_core", source_span_id="s1"),
        _explicit(subject="model_scope", value="single_assembly", source_span_id="s2"),
    ])
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(),
        evidence_ledger=ledger,
    )
    skel = result.skeleton
    assert skel.model_scope.status == "conflict"
    assert "scope" in skel.conflicting_slots


# ---------------------------------------------------------------------------
# Layout mining + deterministic derivation
# ---------------------------------------------------------------------------


def test_assembly_count_from_claim():
    count_claim = _explicit(subject="assembly_count", value=4)
    ledger = _FakeLedger(claims=[count_claim])
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(multi_assembly_core=True),
        evidence_ledger=ledger,
    )
    layout = result.skeleton.assembly_layout
    assert layout.assembly_count == 4
    assert layout.status == "source_backed"
    assert layout.source_claim_ids == [_claim_id(count_claim)]


def test_core_lattice_size_claim_populates_layout():
    ledger = _FakeLedger(claims=[
        _explicit(subject="core_lattice_size", value=[2, 2]),
    ])
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(multi_assembly_core=True),
        evidence_ledger=ledger,
    )
    layout = result.skeleton.assembly_layout
    assert layout.core_lattice_size == (2, 2)


def test_assembly_count_derived_from_lattice_when_missing():
    """assembly_count = rows × cols (deterministic derivation)."""
    ledger = _FakeLedger(claims=[
        _explicit(subject="core_lattice_size", value=[3, 3]),
    ])
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(multi_assembly_core=True),
        evidence_ledger=ledger,
    )
    layout = result.skeleton.assembly_layout
    assert layout.core_lattice_size == (3, 3)
    assert layout.assembly_count == 9
    assert "lattice_product" in layout.derivation_codes


def test_assembly_type_counts_from_claim():
    atc_claim = _explicit(
        subject="assembly_type_counts",
        value={"type_a": 2, "type_b": 2},
    )
    ledger = _FakeLedger(claims=[atc_claim])
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(multi_assembly_core=True),
        evidence_ledger=ledger,
    )
    layout = result.skeleton.assembly_layout
    assert layout.assembly_type_counts == {"type_a": 2, "type_b": 2}


# ---------------------------------------------------------------------------
# Feature flag mining
# ---------------------------------------------------------------------------


def test_feature_flags_from_source_claims():
    ledger = _FakeLedger(claims=[
        _explicit(subject="has_spacer_grids", value=True),
        _explicit(subject="has_axial_geometry", value=True),
        _explicit(subject="has_special_pin_map", value=True),
    ])
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(),
        evidence_ledger=ledger,
    )
    feats = result.skeleton.features
    assert feats.has_spacer_grids is True
    assert feats.has_axial_geometry is True
    assert feats.has_special_pin_map is True


def test_feature_contract_promotes_missing_flags():
    """When a flag claim is missing but the feature contract detects the
    feature, the compiler deterministically derives ``True`` rather than
    fabricating ``False``.
    """
    ledger = _FakeLedger(claims=[])
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(has_spacer_grid=True),
        evidence_ledger=ledger,
    )
    feats = result.skeleton.features
    assert feats.has_spacer_grids is True
    assert feats.status == "deterministically_derived"
    assert "feature_contract.has_spacer_grid" in feats.derivation_codes


def test_source_absence_does_not_become_false():
    """No claim about ``has_spacer_grids`` and no feature contract flag
    must leave the slot ``None``, not ``False``.  This is the rule that
    prevents source absence from being fabricated as a definite negative.
    """
    ledger = _FakeLedger(claims=[])
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(),
        evidence_ledger=ledger,
    )
    feats = result.skeleton.features
    assert feats.has_spacer_grids is None
    assert feats.has_axial_geometry is None
    assert feats.has_special_pin_map is None


# ---------------------------------------------------------------------------
# Fuel variant + localized insert mining
# ---------------------------------------------------------------------------


def test_fuel_variant_claims_become_locked_slots():
    fv1 = _explicit(
        subject="fuel_variant",
        value={
            "variant_id": "region1", "enrichment_wt_percent": 2.11,
            "density_g_cm3": 10.257, "assembly_type_ids": ["A"],
        },
    )
    fv2 = _explicit(
        subject="fuel_variant",
        value={
            "variant_id": "region2", "enrichment_wt_percent": 2.619,
            "density_g_cm3": 10.257, "assembly_type_ids": ["B"],
        },
    )
    ledger = _FakeLedger(claims=[fv1, fv2])
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(has_multiple_fuel_variants=True),
        evidence_ledger=ledger,
    )
    fv = result.skeleton.fuel_variant_slots
    assert len(fv) == 2
    ids = {slot.variant_id for slot in fv}
    assert ids == {"region1", "region2"}
    for slot in fv:
        assert slot.status == "source_backed"
        assert slot.immutable is True


def test_localized_insert_claims_become_locked_slots():
    li1 = _explicit(
        subject="localized_insert",
        value={
            "requirement_id": "pyrex_edge", "insert_kind": "pyrex_rod",
            "assembly_type_ids": ["E"],
            "expected_coordinate_count_per_assembly": 20,
        },
    )
    ledger = _FakeLedger(claims=[li1])
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(has_localized_insert=True),
        evidence_ledger=ledger,
    )
    li = result.skeleton.localized_insert_slots
    assert len(li) == 1
    assert li[0].requirement_id == "pyrex_edge"
    assert li[0].insert_kind == "pyrex_rod"
    assert li[0].status == "source_backed"
    assert li[0].immutable is True


def test_localized_insert_feature_contract_placeholder_when_kind_unknown():
    """When the feature contract says an insert exists but no claim
    identifies the kind, the compiler emits an ``unresolved`` placeholder
    rather than dropping the slot.
    """
    ledger = _FakeLedger(claims=[])
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(has_localized_insert=True),
        evidence_ledger=ledger,
    )
    li = result.skeleton.localized_insert_slots
    assert len(li) == 1
    assert li[0].status == "unresolved"
    assert li[0].unresolved_reason


# ---------------------------------------------------------------------------
# Backwards compatibility
# ---------------------------------------------------------------------------


def test_confirmed_facts_still_override_source_claims():
    """Human-confirmed facts must still win over source claims so the
    human-confirmation loop is preserved.
    """
    ledger = _FakeLedger(claims=[
        _explicit(subject="model_scope", value="multi_assembly_core"),
    ])
    confirmed = {"model_scope": "single_assembly", "assembly_count": 1}
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(),
        evidence_ledger=ledger,
        confirmed_facts=confirmed,
    )
    skel = result.skeleton
    assert skel.model_scope.value == "single_assembly"
    assert skel.model_scope.status == "human_confirmed"
    assert skel.assembly_layout.assembly_count == 1


def test_empty_ledger_yields_unresolved_scope():
    """Backwards compat: empty ledger + empty confirmed_facts +
    no feature contract => scope stays ``unknown`` / ``unresolved``.
    """
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(),
        evidence_ledger=_FakeLedger(claims=[]),
        confirmed_facts={},
    )
    skel = result.skeleton
    assert skel.model_scope.value == "unknown"
    assert skel.model_scope.status == "unresolved"


def test_required_by_json_paths_match_slot():
    """Claims that use ``required_by_json_paths`` instead of subject
    matching still populate the right slot.
    """
    claim = EvidenceClaim(
        claim_id="",
        subject="benchmark_fact",
        predicate="has_value",
        value=4,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(EvidenceSourceRef(source_id="s1", span_id="sp1", excerpt_hash="eh1"),),
        required_by_json_paths=("/assembly_count",),
    )
    ledger = _FakeLedger(claims=[claim])
    result = compile_facts_requirement_skeleton(
        requirement_text="r",
        feature_contract=_FakeFeatureContract(multi_assembly_core=True),
        evidence_ledger=ledger,
    )
    layout = result.skeleton.assembly_layout
    assert layout.assembly_count == 4
    assert _claim_id(claim) in layout.source_claim_ids
