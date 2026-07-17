"""Phase 3B: Placement-owned retry integration."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.placement_issue_policy import placement_owner_patch_types
from openmc_agent.plan_builder.closed_loop.retry_controller import normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_models import RetryTriggerOrigin
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def test_placement_intent_missing_routes_to_pin_map_in_single_assembly() -> None:
    state = PlanBuildState(state_id="place-int", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="pin_map", patch_type="pin_map", content={"patch_type": "pin_map", "lattice_size": [3, 3], "default_universe_id": "fuel", "guide_tube_coords": [[1, 1]], "instrument_tube_coords": [], "localized_insert_intents": []}, status="valid"))
    from openmc_agent.plan_builder.planning_scope import ResolvedPlanningScope
    state.resolved_planning_scope = ResolvedPlanningScope(value="single_assembly", status="resolved")
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_placement_missing"], "reason": "x", "code": "localized_insert.required_placement_missing"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    assert request.owner_patch_types == ["pin_map"]


def test_placement_intent_missing_routes_to_assembly_catalog_in_multi_assembly() -> None:
    state = PlanBuildState(state_id="place-int", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="assembly_catalog", patch_type="assembly_catalog", content={"patch_type": "assembly_catalog", "assembly_types": []}, status="valid"))
    from openmc_agent.plan_builder.planning_scope import ResolvedPlanningScope
    state.resolved_planning_scope = ResolvedPlanningScope(value="multi_assembly_core", status="resolved")
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_placement_missing"], "reason": "x", "code": "localized_insert.required_placement_missing"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    assert request.owner_patch_types == ["assembly_catalog"]
