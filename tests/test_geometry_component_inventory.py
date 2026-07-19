"""Tests for the GeometryComponentInventory compiler."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openmc_agent.plan_investigation.component_evidence import (
    ComponentEvidenceProposal,
    ComponentEvidenceSynthesisResult,
)
from openmc_agent.plan_investigation.errors import PlanInvestigationIssue
from openmc_agent.plan_investigation.evidence_ledger import (
    create_empty_ledger,
    add_claim,
)
from openmc_agent.plan_investigation.evidence_synthesis import (
    bind_synthesis_result_to_ledger,
)
from openmc_agent.plan_investigation.geometry_inventory import (
    INVENTORY_NOT_FACTS_ACCEPTED_CODE,
    AxialRegionRequirement,
    GeometryComponentInventory,
    GeometryInventoryCoverageReport,
    MaterialRoleRequirement,
    RadialLayerRequirement,
    RadialProfileRequirement,
    compile_geometry_component_inventory,
)
from openmc_agent.plan_investigation.models import (
    EvidenceClaim,
    EvidenceSourceRef,
    EvidenceStatus,
    SourceKind,
)
from openmc_agent.plan_investigation.source_index import build_source_index


class _StubVariant:
    def __init__(self, variant_id, source_label=None, assembly_type_ids=None):
        self.variant_id = variant_id
        self.source_label = source_label
        self.assembly_type_ids = assembly_type_ids or []


class _StubInsert:
    def __init__(self, req_id, kind, assembly_type_ids=None, host_kind="guide_tube"):
        self.requirement_id = req_id
        self.insert_kind = kind
        self.assembly_type_ids = assembly_type_ids or []
        self.host_kind = host_kind


class _StubFacts:
    def __init__(self, variants=None, inserts=None):
        self.fuel_variant_requirements = variants or []
        self.localized_insert_requirements = inserts or []
        self.has_axial_geometry = False
        self.model_dump = lambda mode="python": {"stub": True}


_MODEL_FAILURE = (ValidationError, PlanInvestigationIssue)


def _ledger():
    return create_empty_ledger(requirement_hash="rh")


def test_facts_not_accepted_blocks_compilation() -> None:
    with pytest.raises(PlanInvestigationIssue) as exc_info:
        compile_geometry_component_inventory(
            accepted_facts=_StubFacts(),
            evidence_ledger=_ledger(),
            facts_accepted=False,
        )
    assert exc_info.value.code == INVENTORY_NOT_FACTS_ACCEPTED_CODE


def test_facts_none_blocks_compilation() -> None:
    with pytest.raises(PlanInvestigationIssue):
        compile_geometry_component_inventory(
            accepted_facts=None,
            evidence_ledger=_ledger(),
        )


def test_fuel_variants_compile_to_active_fuel_pin_profiles() -> None:
    facts = _StubFacts(variants=[_StubVariant("v1"), _StubVariant("v2")])
    inv = compile_geometry_component_inventory(
        accepted_facts=facts, evidence_ledger=_ledger()
    )
    # Two fuel variants → two active_fuel_pin profiles.
    fuel_profiles = [p for p in inv.radial_profiles if p.profile_kind == "active_fuel_pin"]
    assert len(fuel_profiles) == 2
    # Each has a corresponding fuel material role requirement.
    fuel_roles = [m for m in inv.material_role_requirements if m.role == "fuel"]
    assert len(fuel_roles) == 2
    # Each fuel role carries a distinct fuel_variant_id.
    variant_ids = {m.fuel_variant_id for m in fuel_roles}
    assert variant_ids == {"v1", "v2"}


def test_localized_inserts_compile_to_distinct_profiles() -> None:
    facts = _StubFacts(inserts=[
        _StubInsert("cr1", "control_rod"),
        _StubInsert("px1", "pyrex_rod"),
    ])
    inv = compile_geometry_component_inventory(
        accepted_facts=facts, evidence_ledger=_ledger()
    )
    profiles_by_kind = {p.component_kind: p for p in inv.radial_profiles}
    assert "control_rod" in profiles_by_kind
    assert "pyrex_rod" in profiles_by_kind
    # Poison and absorber are NEVER merged.
    absorber_reqs = [m for m in inv.material_role_requirements if m.role == "absorber"]
    poison_reqs = [m for m in inv.material_role_requirements if m.role == "poison"]
    assert len(absorber_reqs) == 1
    assert len(poison_reqs) == 1


def test_has_axial_geometry_alone_does_not_generate_end_plugs() -> None:
    """The legacy blanket rule (has_axial_geometry → end_plug, gas_gap,
    water_pin) must NOT fire without explicit component evidence.
    """
    facts = _StubFacts()
    facts.has_axial_geometry = True  # set the legacy flag
    inv = compile_geometry_component_inventory(
        accepted_facts=facts, evidence_ledger=_ledger()
    )
    component_kinds = {p.component_kind for p in inv.radial_profiles}
    assert "end_plug" not in component_kinds
    assert "gas_gap" not in component_kinds
    assert "water_pin" not in component_kinds


def test_end_plug_generated_only_with_explicit_claim() -> None:
    """A source-backed component_present claim for end_plug produces a profile."""
    facts = _StubFacts()
    ld = _ledger()
    # Add an explicit claim that says end_plug is present.
    idx = build_source_index(text="end plug present.\n", title="t", source_kind=SourceKind.USER_REQUIREMENT)
    span = idx.make_span(1, 1)
    idx.register_span(span)
    from openmc_agent.plan_investigation.models import EvidenceSourceRef
    claim = EvidenceClaim(
        claim_id="",
        subject="end_plug",
        predicate="geometry.profile_required",
        value=True,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(EvidenceSourceRef(source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash),),
        metadata={"component_kind": "end_plug", "profile_kind": "fuel_rod_end_plug"},
    )
    add_claim(ld, claim, source_indexes={idx.document.source_id: idx})
    inv = compile_geometry_component_inventory(
        accepted_facts=facts, evidence_ledger=ld, source_indexes={idx.document.source_id: idx}
    )
    component_kinds = {p.component_kind for p in inv.radial_profiles}
    assert "end_plug" in component_kinds


def test_identical_end_plug_profiles_deduplicated() -> None:
    """Upper/lower end plugs with the same cross-section share one profile."""
    facts = _StubFacts()
    ld = _ledger()
    idx = build_source_index(text="upper end plug\nlower end plug\n", title="t", source_kind=SourceKind.USER_REQUIREMENT)
    span1 = idx.make_span(1, 1); idx.register_span(span1)
    span2 = idx.make_span(2, 2); idx.register_span(span2)
    for span in (span1, span2):
        claim = EvidenceClaim(
            claim_id="",
            subject="end_plug",
            predicate="geometry.profile_required",
            value=True,
            status=EvidenceStatus.EXPLICIT,
            source_refs=(EvidenceSourceRef(source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash),),
            metadata={"component_kind": "end_plug", "profile_kind": "fuel_rod_end_plug"},
        )
        add_claim(ld, claim, source_indexes={idx.document.source_id: idx})
    inv = compile_geometry_component_inventory(
        accepted_facts=facts, evidence_ledger=ld, source_indexes={idx.document.source_id: idx}
    )
    end_plug_profiles = [p for p in inv.radial_profiles if p.component_kind == "end_plug"]
    assert len(end_plug_profiles) == 1


def test_axial_region_present_claim_creates_axial_region() -> None:
    facts = _StubFacts()
    ld = _ledger()
    idx = build_source_index(text="plenum region\n", title="t", source_kind=SourceKind.USER_REQUIREMENT)
    span = idx.make_span(1, 1); idx.register_span(span)
    claim = EvidenceClaim(
        claim_id="",
        subject="plenum",
        predicate="geometry.axial_region_present",
        value=True,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(EvidenceSourceRef(source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash),),
        metadata={"component_kind": "plenum", "axial_region_kind": "plenum"},
    )
    add_claim(ld, claim, source_indexes={idx.document.source_id: idx})
    inv = compile_geometry_component_inventory(
        accepted_facts=facts, evidence_ledger=ld, source_indexes={idx.document.source_id: idx}
    )
    assert len(inv.axial_regions) >= 1
    assert any(r.region_kind == "plenum" for r in inv.axial_regions)


def test_inventory_hash_deterministic() -> None:
    facts = _StubFacts(variants=[_StubVariant("v1")])
    ld1 = _ledger()
    ld2 = _ledger()
    inv1 = compile_geometry_component_inventory(accepted_facts=facts, evidence_ledger=ld1)
    inv2 = compile_geometry_component_inventory(accepted_facts=facts, evidence_ledger=ld2)
    assert inv1.inventory_hash == inv2.inventory_hash


def test_inventory_hash_changes_with_facts() -> None:
    facts_a = _StubFacts(variants=[_StubVariant("v1")])
    facts_b = _StubFacts(variants=[_StubVariant("v1"), _StubVariant("v2")])
    inv_a = compile_geometry_component_inventory(accepted_facts=facts_a, evidence_ledger=_ledger())
    inv_b = compile_geometry_component_inventory(accepted_facts=facts_b, evidence_ledger=_ledger())
    assert inv_a.inventory_hash != inv_b.inventory_hash


def test_declared_material_roles_property() -> None:
    facts = _StubFacts(variants=[_StubVariant("v1")], inserts=[
        _StubInsert("cr1", "control_rod"),
    ])
    inv = compile_geometry_component_inventory(accepted_facts=facts, evidence_ledger=_ledger())
    roles = set(inv.declared_material_roles)
    assert "fuel" in roles
    assert "absorber" in roles


def test_declared_component_kinds_property() -> None:
    facts = _StubFacts(variants=[_StubVariant("v1")])
    inv = compile_geometry_component_inventory(accepted_facts=facts, evidence_ledger=_ledger())
    kinds = set(inv.declared_component_kinds)
    assert "fuel_pin" in kinds


def test_inventory_has_no_reactor_specific_branches() -> None:
    """Production code must not contain VERA / PWR / BWR branches in
    executable statements.  Docstrings describing what's excluded are OK.
    """
    import ast
    import inspect
    from openmc_agent.plan_investigation import geometry_inventory as mod

    src = inspect.getsource(mod)
    tree = ast.parse(src)
    # Walk only string literals inside call expressions / assignments;
    # skip standalone docstrings.
    forbidden = ("vera3", "vera4", "pwr_", "bwr_", "vver_", "htgr_", "sfr_", "candu_")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value_lower = node.value.lower()
            for term in forbidden:
                assert term not in value_lower, (
                    f"forbidden term '{term}' in string literal: {node.value!r}"
                )


def test_radial_profile_rejects_invalid_component_kind() -> None:
    with pytest.raises(_MODEL_FAILURE):
        RadialProfileRequirement(
            profile_id="p1",
            profile_kind="active_fuel_pin",
            component_kind="pwr_fuel_pin",  # not in ontology
        )


def test_radial_profile_rejects_invalid_profile_kind() -> None:
    with pytest.raises(_MODEL_FAILURE):
        RadialProfileRequirement(
            profile_id="p1",
            profile_kind="pwr_active_pin",  # not in ontology
            component_kind="fuel_pin",
        )
