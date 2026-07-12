from openmc_agent.plan_builder.validation_repair import PatchRepairRequest
from openmc_agent.plan_builder.validation_repair_prompts import build_patch_repair_prompt


def test_repair_prompt_contains_previous_patch_and_exact_schema_path() -> None:
    request = PatchRepairRequest(repair_id="r", issue_fingerprint="f", target_patch_type="pin_map", issues=[{"code":"lattice.pin_count_mismatch", "schema_path":"complex_model.lattices.x.universe_pattern", "message":"m"}], previous_patch_content={"patch_type":"pin_map", "default_universe_id":"bad"}, previous_patch_hash="h", relevant_plan_fragment={"value":"bad"}, valid_upstream_patch_summaries={}, allowed_path_patterns=["/default_universe_id"], forbidden_path_patterns=[])
    prompt = build_patch_repair_prompt(request)
    assert "complex_model.lattices.x.universe_pattern" in prompt
    assert '"default_universe_id": "bad"' in prompt
    assert "/default_universe_id" in prompt
    assert "complete SimulationPlan" in prompt
    assert "All five top-level keys are required" in prompt
    assert '"rationale"' in prompt
    assert '"confidence": 0.0' in prompt
