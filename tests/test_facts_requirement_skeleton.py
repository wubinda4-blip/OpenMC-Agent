"""Tests for FactsRequirementSkeleton compilation and models."""

from openmc_agent.plan_builder.facts_requirement_skeleton import (
    FactsRequirementSlot,
    FactsScopeRequirement,
    FactsAssemblyLayoutRequirement,
    FactsFeatureRequirement,
    FactsFuelVariantSlot,
    FactsLocalizedInsertSlot,
    FactsCountRequirement,
    FactsRequirementSkeleton,
    FactsSkeletonCompilationResult,
    compile_facts_requirement_skeleton,
)


def test_slot_default_status_unresolved():
    slot = FactsRequirementSlot(slot_id="test", facts_json_path="/test")
    assert slot.status == "unresolved"
    assert slot.confidence == 0.0


def test_scope_requirement_defaults():
    scope = FactsScopeRequirement()
    assert scope.value == "unknown"
    assert scope.status == "unresolved"
    assert scope.required is True


def test_assembly_layout_defaults():
    layout = FactsAssemblyLayoutRequirement()
    assert layout.assembly_count is None
    assert layout.core_lattice_size is None
    assert layout.status == "unresolved"


def test_feature_requirement_defaults():
    feat = FactsFeatureRequirement()
    assert feat.has_axial_geometry is None
    assert feat.has_spacer_grids is None
    assert feat.status == "unresolved"


def test_fuel_variant_slot():
    slot = FactsFuelVariantSlot(
        slot_id="fv_fuel_a",
        variant_id="fuel_a",
        enrichment_wt_percent=3.1,
    )
    assert slot.variant_id == "fuel_a"
    assert slot.enrichment_wt_percent == 3.1
    assert slot.status == "unresolved"


def test_localized_insert_slot():
    slot = FactsLocalizedInsertSlot(
        slot_id="li_pyrex",
        requirement_id="pyrex_req",
        insert_kind="pyrex_rod",
    )
    assert slot.requirement_id == "pyrex_req"
    assert slot.insert_kind == "pyrex_rod"


def test_count_requirement():
    slot = FactsCountRequirement(
        slot_id="count_0",
        role="fuel_pin",
        scope="assembly_type",
        value=264,
        assembly_type_id="type_a",
    )
    assert slot.role == "fuel_pin"
    assert slot.value == 264
    assert slot.scope == "assembly_type"


def test_skeleton_creation():
    scope = FactsScopeRequirement(
        value="multi_assembly_core",
        status="source_backed",
        confidence=0.9,
    )
    skeleton = FactsRequirementSkeleton(
        requirement_hash="abc",
        source_index_hash="def",
        ledger_hash="ghi",
        feature_contract_hash="jkl",
        model_scope=scope,
    )
    assert skeleton.model_scope is not None
    assert skeleton.model_scope.value == "multi_assembly_core"
    assert skeleton.skeleton_hash


def test_compile_without_evidence():
    result = compile_facts_requirement_skeleton(
        requirement_text="test requirement",
        feature_contract=None,
    )
    assert result is not None
    assert result.skeleton is not None
    assert result.skeleton.model_scope is not None
    assert result.skeleton.model_scope.status == "unresolved"
    assert result.skeleton.model_scope.value == "unknown"


def test_compile_with_confirmed_facts():
    confirmed = {
        "model_scope": "multi_assembly_core",
        "assembly_count": 9,
        "has_spacer_grids": True,
    }
    result = compile_facts_requirement_skeleton(
        requirement_text="test multi-assembly",
        feature_contract=None,
        confirmed_facts=confirmed,
    )
    assert result.ok
    sk = result.skeleton
    assert sk is not None
    assert sk.model_scope is not None
    assert sk.model_scope.value == "multi_assembly_core"
    assert sk.model_scope.status == "human_confirmed"
    assert sk.assembly_layout is not None
    assert sk.assembly_layout.assembly_count == 9
    assert sk.features is not None
    assert sk.features.has_spacer_grids is True
