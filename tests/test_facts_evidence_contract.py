"""Tests for FactsEvidenceContract and skeleton preflight."""

from openmc_agent.plan_builder.facts_requirement_skeleton import (
    FactsRequirementSkeleton,
    FactsScopeRequirement,
    FactsFeatureRequirement,
    FactsAssemblyLayoutRequirement,
    FactsFuelVariantSlot,
)
from openmc_agent.plan_builder.facts_evidence_contract import (
    FactsEvidenceContract,
    compile_facts_evidence_contract,
    run_facts_skeleton_preflight,
    FactsContentProposal,
    merge_facts_content_into_skeleton,
)


def test_contract_defaults():
    contract = FactsEvidenceContract()
    assert contract.resolved_scope == "unknown"
    assert contract.required_feature_flags == []
    assert contract.required_assembly_layout is False


def test_compile_contract_from_skeleton():
    scope = FactsScopeRequirement(
        value="multi_assembly_core",
        status="human_confirmed",
        confidence=1.0,
        immutable=True,
    )
    layout = FactsAssemblyLayoutRequirement(
        assembly_count=9,
        core_lattice_size=(3, 3),
        status="human_confirmed",
    )
    skeleton = FactsRequirementSkeleton(
        requirement_hash="abc",
        source_index_hash="def",
        ledger_hash="ghi",
        feature_contract_hash="jkl",
        model_scope=scope,
        assembly_layout=layout,
    )
    contract = compile_facts_evidence_contract(skeleton)
    assert contract.resolved_scope == "multi_assembly_core"
    assert contract.required_assembly_layout is True
    assert contract.evidence_contract_hash


def test_compile_contract_none_skeleton():
    contract = compile_facts_evidence_contract(None)
    assert contract.resolved_scope == "unknown"


def test_preflight_passes_with_matching_scope():
    scope = FactsScopeRequirement(
        value="single_assembly",
        status="human_confirmed",
        immutable=True,
    )
    skeleton = FactsRequirementSkeleton(
        requirement_hash="abc",
        source_index_hash="def",
        ledger_hash="ghi",
        feature_contract_hash="jkl",
        model_scope=scope,
    )
    result = run_facts_skeleton_preflight(
        skeleton, {"model_scope": "single_assembly"}
    )
    assert result.ok
    assert len(result.issues) == 0


def test_preflight_detects_scope_contradiction():
    scope = FactsScopeRequirement(
        value="multi_assembly_core",
        status="source_backed",
        confidence=0.9,
        immutable=True,
    )
    skeleton = FactsRequirementSkeleton(
        requirement_hash="abc",
        source_index_hash="def",
        ledger_hash="ghi",
        feature_contract_hash="jkl",
        model_scope=scope,
    )
    result = run_facts_skeleton_preflight(
        skeleton, {"model_scope": "single_assembly"}
    )
    assert not result.ok
    codes = {i["code"] for i in result.issues}
    assert "facts_skeleton.immutable_field_modified" in codes


def test_merge_preserves_immutable_fields():
    scope = FactsScopeRequirement(
        value="multi_assembly_core",
        status="human_confirmed",
        immutable=True,
    )
    skeleton = FactsRequirementSkeleton(
        requirement_hash="abc",
        source_index_hash="def",
        ledger_hash="ghi",
        feature_contract_hash="jkl",
        model_scope=scope,
    )
    proposal = FactsContentProposal(
        resolved_fields={"model_scope": "single_assembly"},
    )
    result = merge_facts_content_into_skeleton(skeleton, proposal)
    assert result.merged is not None
    assert result.merged.patch.get("model_scope") == "multi_assembly_core"


def test_preflight_missing_candidate():
    result = run_facts_skeleton_preflight(None, None)
    assert not result.ok
    assert any("facts_skeleton.missing" in i["code"] for i in result.issues)
