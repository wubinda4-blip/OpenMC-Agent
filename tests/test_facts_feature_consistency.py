from openmc_agent.plan_builder.planning_scope import planning_feature_contract
from openmc_agent.plan_builder.closed_loop.facts_consistency import run_facts_consistency_preflight


def test_source_critical_features_cannot_be_erased_from_facts():
    contract = planning_feature_contract({"feature_summary": {"multi_assembly_core": True, "has_spacer_grid": True, "has_localized_insert": True, "has_multi_segment_localized_insert": True, "has_control_state": True, "has_multiple_fuel_variants": True}})
    result = run_facts_consistency_preflight(feature_contract=contract, facts_patch={"patch_type":"facts", "model_scope":"single_assembly", "has_spacer_grids":False, "localized_insert_requirements":[], "fuel_variant_requirements":[]})
    codes = {item["code"] for item in result.issues}
    assert {"facts.model_scope_conflicts_with_planning_features", "facts.localized_insert_contract_missing", "facts.localized_insert_profile_contract_missing", "facts.spacer_grid_contract_missing", "facts.fuel_variant_contract_missing"} <= codes


def test_unknown_counts_do_not_downgrade_multi_scope():
    contract = planning_feature_contract({"feature_summary": {"multi_assembly_core": True}})
    result = run_facts_consistency_preflight(feature_contract=contract, facts_patch={"patch_type":"facts", "model_scope":"multi_assembly_core"})
    assert result.scope.value == "multi_assembly_core"
    assert "facts.multi_assembly_contract_incomplete" in {item["code"] for item in result.issues}
