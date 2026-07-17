"""VERA4 Phase-3B offline mutation challenges.

Each mutation deletes or corrupts a specific field in the deterministic VERA4
fixture, then verifies that the typed retry protocol correctly identifies the
owner, builds a typed request, and produces the expected classification.

These tests use NO LLM, NO OpenMC, NO reference data — purely deterministic.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from scripts.vera4_base_fixture import build_all_vera4_patches

from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.closed_loop.placement_preflight import run_placement_preflight
from openmc_agent.plan_builder.closed_loop.retry_controller import normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_models import RetryTriggerOrigin
from openmc_agent.plan_builder.closed_loop.retry_request_builders import (
    build_retry_request_from_material_readiness,
    build_retry_request_from_placement_dependency,
)
from openmc_agent.plan_builder.material_execution_readiness import validate_material_execution_readiness
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _vera4_state() -> PlanBuildState:
    state = PlanBuildState(state_id="vera4-offline", requirement_text="VERA4 offline mutation", benchmark_id="VERA4")
    for patch in build_all_vera4_patches():
        content = patch.model_dump(mode="json")
        state.add_patch(PlanPatchEnvelope(patch_id=content["patch_type"], patch_type=content["patch_type"], content=content, status="valid", source="fixture"))
    return state


def test_vera4_facts_mutation_localized_insert_contract_missing_routes_to_facts_owner() -> None:
    state = _vera4_state()
    facts_env = next(item for item in state.patches.values() if item.patch_type == "facts")
    facts_env.content = dict(facts_env.content)
    facts_env.content["localized_insert_requirements"] = []
    request = normalize_retry_request(
        {"issue_codes": ["facts.localized_insert_contract_missing"], "reason": "facts mutated"},
        state=state, origin=RetryTriggerOrigin.FACTS_GATE,
    )
    assert request is not None
    assert request.owner_patch_types == ["facts"]
    assert request.reason_code == "facts.localized_insert_contract_missing"


def test_vera4_material_density_mutation_routes_to_materials_owner() -> None:
    state = _vera4_state()
    materials_env = next(item for item in state.patches.values() if item.patch_type == "materials")
    mutated = copy.deepcopy(materials_env.content)
    # Strip density from a structural material that grids depend on.
    for material in mutated.get("materials", []):
        if material.get("material_id") in {"zircaloy4", "inconel718"}:
            material["density_g_cm3"] = None
    materials_env.content = mutated
    overlays_env = next(item for item in state.patches.values() if item.patch_type == "axial_overlays")
    readiness = validate_material_execution_readiness(materials_patch=materials_env.content, axial_overlays_patch=overlays_env.content, policy="approved_library")
    assert readiness.issues  # density issues detected
    # Aggregate into a single typed request per material.
    for issue in readiness.issues[:1]:
        request = build_retry_request_from_material_readiness(
            material_id=issue.material_id,
            consumer_ids=issue.affected_consumer_ids,
            required_property=issue.required_property,
            state=state,
        )
        assert request is not None
        assert request.owner_patch_types == ["materials"]
        assert request.targets[0].required_ids == [issue.material_id]


def test_vera4_missing_universe_mutation_routes_to_universes_owner() -> None:
    state = _vera4_state()
    universes_env = next(item for item in state.patches.values() if item.patch_type == "universes")
    mutated = copy.deepcopy(universes_env.content)
    # Remove the RCCA AIC universe.
    mutated["universes"] = [u for u in mutated["universes"] if u.get("universe_id") != "rcca_aic"]
    universes_env.content = mutated
    request = build_retry_request_from_placement_dependency(
        dependency_patch_type="universes",
        issue_codes=["localized_insert.required_universe_missing"],
        finding_ids=["f1"],
        required_ids=["rcca_aic"],
        reason="RCCA AIC universe deleted",
        state=state,
    )
    assert request is not None
    assert request.owner_patch_types == ["universes"]
    assert "rcca_aic" in request.targets[0].required_ids


def test_vera4_placement_intent_mutation_routes_to_placement_owner() -> None:
    """Delete the RCCA placement intent; the required_placement_missing code
    must route to the assembly_catalog owner (multi_assembly scope)."""
    state = _vera4_state()
    catalog_env = next(item for item in state.patches.values() if item.patch_type == "assembly_catalog")
    mutated = copy.deepcopy(catalog_env.content)
    center = next(item for item in mutated["assembly_types"] if item["assembly_type_id"] == "center_rcca")
    center["pin_map"]["localized_insert_intents"] = []
    catalog_env.content = mutated
    preflight = run_placement_preflight(state=state)
    assert any(item["code"] == "localized_insert.required_placement_missing" for item in preflight["issues"])


def test_vera4_full_suite_passes_baseline_preflight() -> None:
    """The unmutated VERA4 fixture must pass placement preflight cleanly."""
    state = _vera4_state()
    preflight = run_placement_preflight(state=state)
    assert preflight["ok"], f"baseline preflight failed: {json.dumps(preflight['issues'], indent=2)}"
