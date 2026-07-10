from openmc_agent.semantic_audit import build_semantic_audit_input


def test_audit_input_redacts_secrets_and_compacts_map():
    pattern = [["fuel" for _ in range(17)] for _ in range(17)]
    ai = build_semantic_audit_input(requirement="api_key=SECRET 2D", resolved_requirement=None, workflow_state={"simulation_plan":{"complex_model":{"lattices":[{"id":"lat", "universe_pattern": pattern}]}}})
    dumped = ai.model_dump_json()
    assert "SECRET" not in dumped
    assert dumped.count("fuel") < 50
    assert ai.assembled_plan_summary["lattices"][0]["dimensions"] == [17,17]
