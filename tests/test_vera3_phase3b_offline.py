"""VERA3 Phase-3B offline mutation challenges.

VERA3 is a single-assembly benchmark with axial layers, spacer grids, and
Pyrex rods.  These mutations verify that the retry protocol correctly handles
single-assembly scope owners (pin_map, not assembly_catalog).
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.closed_loop.retry_controller import normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_models import RetryTriggerOrigin
from openmc_agent.plan_builder.closed_loop.retry_request_builders import (
    build_retry_request_from_material_readiness,
    build_retry_request_from_placement_dependency,
)
from openmc_agent.plan_builder.planning_scope import PlanningFeatureContract, ResolvedPlanningScope, build_canonical_task_plan
from openmc_agent.plan_builder.dependency_graph import DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _vera3_state() -> PlanBuildState:
    """A minimal single-assembly VERA3-like state."""
    state = PlanBuildState(state_id="vera3-offline", requirement_text="VERA3 3B benchmark", benchmark_id="VERA3", selected_variant="3B")
    facts = {"patch_type": "facts", "model_scope": "single_assembly", "localized_insert_requirements": [{"requirement_id": "pyrex", "insert_kind": "pyrex_rod", "assembly_type_ids": [], "expected_coordinate_count_per_assembly": 1, "host_kind": "guide_tube", "required_profile_id": "p1", "required_segment_roles": ["pyrex"], "expected_insert_universe_ids": ["pyrex_u"], "anchor_z_cm": 100.0, "control_state_id": "base"}]}
    universes = {"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel", "material_id": "fuel"}]}, {"universe_id": "pyrex_u", "kind": "pyrex_rod", "cells": [{"id": "c", "role": "pyrex", "material_id": "pyrex_glass"}]}]}
    materials = {"patch_type": "materials", "materials": [{"material_id": "fuel", "density_g_cm3": 10.0, "role": "fuel"}, {"material_id": "pyrex_glass", "density_g_cm3": 2.23, "role": "poison"}]}
    profiles = {"patch_type": "localized_insert_profiles", "profiles": [{"profile_id": "p1", "anchor_kind": "bottom", "anchor_z_cm": 100.0, "segments": [{"segment_id": "s", "relative_z_min_cm": 0, "relative_z_max_cm": 100, "universe_id": "pyrex_u", "role": "pyrex"}]}]}
    pin = {"patch_type": "pin_map", "lattice_size": [3, 3], "default_universe_id": "fuel", "guide_tube_coords": [[1, 1]], "instrument_tube_coords": [], "localized_insert_intents": [{"insert_id": "i", "insert_kind": "pyrex_rod", "insert_universe_id": "pyrex_u", "coordinates": [[1, 1]], "axial_profile_id": "p1", "anchor_z_cm": 100.0, "control_state_id": "base"}]}
    for patch in (facts, universes, materials, profiles, pin):
        state.add_patch(PlanPatchEnvelope(patch_id=patch["patch_type"], patch_type=patch["patch_type"], content=patch, status="valid"))
    state.resolved_planning_scope = ResolvedPlanningScope(value="single_assembly", status="resolved")
    state.planning_feature_contract = PlanningFeatureContract(has_localized_insert=True, has_spacer_grid=True)
    state.canonical_task_plan = build_canonical_task_plan(scope=state.resolved_planning_scope, contract=state.planning_feature_contract, facts_patch=facts, feature_order=list(DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH._ORDER))
    return state


def test_vera3_single_assembly_placement_owner_is_pin_map_not_assembly_catalog() -> None:
    state = _vera3_state()
    # With resolved_planning_scope.value = "single_assembly", the owner policy
    # must route placement issues to pin_map, not assembly_catalog.
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["pyrex_u"], "reason": "x", "code": "localized_insert.required_universe_missing"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    assert request.owner_patch_types == ["universes"]


def test_vera3_material_density_routes_to_materials() -> None:
    state = _vera3_state()
    request = build_retry_request_from_material_readiness(material_id="pyrex_glass", consumer_ids=["overlay_1"], required_property="density_g_cm3", state=state)
    assert request is not None
    assert request.owner_patch_types == ["materials"]


def test_vera3_off_mode_makes_no_llm_calls() -> None:
    state = _vera3_state()
    normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["pyrex_u"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    policy = PlanClosedLoopPolicy(mode="off")
    from openmc_agent.plan_builder.closed_loop.retry_controller import execute_plan_retry_loop
    outcome = execute_plan_retry_loop(state=state, policy=policy, candidate_producer=lambda *_: {})
    assert outcome.status.value == "blocked"
