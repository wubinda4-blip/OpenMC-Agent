"""Phase 3B: Materials owner integration — targeted density revision."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.retry_request_builders import build_retry_request_from_material_readiness
from openmc_agent.plan_builder.state import PlanBuildState


def test_materials_request_aggregates_consumers_and_targets_only_density_fields() -> None:
    state = PlanBuildState(state_id="mat-int", requirement_text="r")
    request = build_retry_request_from_material_readiness(
        material_id="zircaloy4",
        consumer_ids=["grid_1", "grid_2", "grid_3"],
        required_property="density_g_cm3",
        state=state,
    )
    assert request is not None
    assert request.owner_patch_types == ["materials"]
    assert sorted(request.consumer_ids) == ["grid_1", "grid_2", "grid_3"]
    assert request.targets[0].required_ids == ["zircaloy4"]
    assert "/materials/zircaloy4" in request.targets[0].affected_json_paths


def test_multiple_materials_produce_distinct_requests() -> None:
    state = PlanBuildState(state_id="mat-int", requirement_text="r")
    r1 = build_retry_request_from_material_readiness(material_id="zircaloy4", consumer_ids=["g1"], required_property="density_g_cm3", state=state)
    r2 = build_retry_request_from_material_readiness(material_id="inconel718", consumer_ids=["g2"], required_property="density_g_cm3", state=state)
    assert r1 is not None and r2 is not None
    assert r1.request_id != r2.request_id
    assert r1.request_fingerprint != r2.request_fingerprint
