"""Tests for FactsEvidenceContract and skeleton preflight."""

from openmc_agent.plan_builder.facts_requirement_skeleton import (
    FactsRequirementSkeleton,
    FactsScopeRequirement,
    FactsFeatureRequirement,
    FactsAssemblyLayoutRequirement,
    FactsFuelVariantSlot,
    FactsLocalizedInsertSlot,
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


# ---------------------------------------------------------------------------
# Phase 8C Step 2 — merge contract enforcement
# ---------------------------------------------------------------------------


def test_merge_locks_deterministically_derived_scope():
    """Phase 8C Step 2: ``deterministically_derived`` scope (e.g. feature
    contract says multi-assembly) locks the slot — the LLM proposal
    cannot downgrade it to single_assembly.
    """
    scope = FactsScopeRequirement(
        value="multi_assembly_core",
        status="deterministically_derived",
        derivation_codes=["feature_contract.multi_assembly_core"],
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


def test_merge_does_not_lock_conflict_scope():
    """When the skeleton scope is in ``conflict`` status, the LLM
    proposal is used (and a warning is emitted).  The conflict surfaces
    as a preflight warning rather than being silently resolved.
    """
    scope = FactsScopeRequirement(
        value="unknown",
        status="conflict",
        unresolved_reason="conflicting source claims",
    )
    skeleton = FactsRequirementSkeleton(
        requirement_hash="abc",
        source_index_hash="def",
        ledger_hash="ghi",
        feature_contract_hash="jkl",
        model_scope=scope,
    )
    proposal = FactsContentProposal(
        resolved_fields={"model_scope": "multi_assembly_core"},
    )
    result = merge_facts_content_into_skeleton(skeleton, proposal)
    assert result.merged is not None
    assert result.merged.patch.get("model_scope") == "multi_assembly_core"
    assert any("conflict" in w for w in result.warnings)


def test_merge_locks_fuel_variants_from_skeleton():
    """Fuel variant slots from the skeleton (e.g. mined from claims)
    appear in the candidate patch — the LLM cannot drop them.
    """
    fv_slot = FactsFuelVariantSlot(
        slot_id="fv_region1",
        variant_id="region1",
        enrichment_wt_percent=2.11,
        density_g_cm3=10.257,
        assembly_type_ids=["A"],
        status="source_backed",
        immutable=True,
    )
    skeleton = FactsRequirementSkeleton(
        requirement_hash="abc",
        source_index_hash="def",
        ledger_hash="ghi",
        feature_contract_hash="jkl",
        fuel_variant_slots=[fv_slot],
    )
    proposal = FactsContentProposal(
        resolved_fields={
            "fuel_variant_requirements": [
                {"variant_id": "region2", "enrichment_wt_percent": 3.0}
            ]
        },
    )
    result = merge_facts_content_into_skeleton(skeleton, proposal)
    assert result.merged is not None
    fv = result.merged.patch.get("fuel_variant_requirements", [])
    # Source-backed variant is preserved; the LLM's variant is added by the
    # override loop because the proposal's path matches a non-locked slot
    # (the locked fuel variant slots are not at the field_path level).
    # Either way the source-backed variant must be present.
    variant_ids = [v.get("variant_id") for v in fv if isinstance(v, dict)]
    assert "region1" in variant_ids


def test_merge_locks_localized_inserts_from_skeleton():
    """Localized insert slots from the skeleton appear in the candidate."""
    li_slot = FactsLocalizedInsertSlot(
        slot_id="li_pyrex",
        requirement_id="pyrex_edge",
        insert_kind="pyrex_rod",
        assembly_type_ids=["E"],
        expected_coordinate_count_per_assembly=20,
        status="source_backed",
        immutable=True,
    )
    skeleton = FactsRequirementSkeleton(
        requirement_hash="abc",
        source_index_hash="def",
        ledger_hash="ghi",
        feature_contract_hash="jkl",
        localized_insert_slots=[li_slot],
    )
    proposal = FactsContentProposal(resolved_fields={})
    result = merge_facts_content_into_skeleton(skeleton, proposal)
    li = result.merged.patch.get("localized_insert_requirements", [])
    assert len(li) == 1
    assert li[0].get("requirement_id") == "pyrex_edge"
    assert li[0].get("insert_kind") == "pyrex_rod"
    assert li[0].get("expected_coordinate_count_per_assembly") == 20


def test_merge_normalizes_blank_control_state_id_to_base():
    skeleton = FactsRequirementSkeleton(
        requirement_hash="abc",
        source_index_hash="def",
        ledger_hash="ghi",
        feature_contract_hash="jkl",
    )
    proposal = FactsContentProposal(
        resolved_fields={
            "localized_insert_requirements": [
                {
                    "requirement_id": "rcca",
                    "insert_kind": "control_rod",
                    "control_state_id": "",
                }
            ]
        }
    )
    result = merge_facts_content_into_skeleton(skeleton, proposal)
    li = result.merged.patch["localized_insert_requirements"]
    assert li[0]["control_state_id"] == "base"
