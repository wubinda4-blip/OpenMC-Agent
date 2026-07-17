from openmc_agent.plan_builder.planning_scope import planning_feature_contract, resolve_planning_scope, build_canonical_task_plan


def _decision(**summary): return {"feature_summary": summary}


def test_feature_multi_and_single_facts_is_a_blocking_conflict():
    resolved = resolve_planning_scope(planning_mode_decision=_decision(multi_assembly_core=True), facts_patch={"model_scope": "single_assembly"}, existing_valid_patch_types=[], confirmed_facts={})
    assert resolved.status == "conflict"


def test_one_assembly_type_does_not_imply_single_assembly():
    resolved = resolve_planning_scope(planning_mode_decision=_decision(), facts_patch={"model_scope": "multi_assembly_core", "assembly_type_counts": {"A": 9}}, existing_valid_patch_types=[], confirmed_facts={})
    assert resolved.status == "resolved" and resolved.value == "multi_assembly_core"


def test_canonical_multi_plan_uses_catalog_not_top_level_pin_map():
    contract = planning_feature_contract(_decision(multi_assembly_core=True, has_axial_geometry=True))
    scope = resolve_planning_scope(planning_mode_decision=_decision(multi_assembly_core=True), facts_patch={"model_scope": "multi_assembly_core", "assembly_count": 4, "core_lattice_size": [2,2], "assembly_type_counts": {"A":4}}, existing_valid_patch_types=[], confirmed_facts={})
    plan = build_canonical_task_plan(scope=scope, contract=contract, facts_patch={"model_scope":"multi_assembly_core"}, feature_order=["facts","materials","universes","pin_map","assembly_catalog","core_layout","settings"])
    assert "assembly_catalog" in plan.required_patch_types and "core_layout" in plan.required_patch_types
    assert "pin_map" not in plan.required_patch_types
