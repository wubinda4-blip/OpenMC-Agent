from openmc_agent.plan_builder.root_cause_classifier import classify_planning_root_causes, record_targeted_retry_attempt
from openmc_agent.plan_builder.state import PlanBuildState


def test_scope_is_prioritized_to_facts_owner():
    causes = classify_planning_root_causes([{"code":"assembly.missing_patch"}], {"facts":"a"})
    assert causes[0].code == "facts.model_scope_conflicts_with_planning_features"
    assert causes[0].owner_patch_types == ["facts"]


def test_repeated_same_material_candidate_stops_on_second_attempt():
    state = PlanBuildState(state_id="s", requirement_text="r")
    cause = classify_planning_root_causes([{"code":"materials.execution_density_required", "material_id":"steel"}], {"materials":"same"})[0]
    assert not record_targeted_retry_attempt(state, cause)["no_progress"]
    assert record_targeted_retry_attempt(state, cause)["no_progress"]
