"""Phase 3B: typed retry request fidelity (no fields lost in translation)."""

from __future__ import annotations

import pytest

from openmc_agent.plan_builder.closed_loop.retry_controller import normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_models import (
    ExecutablePlanRetryRequest,
    RetryTriggerOrigin,
)
from openmc_agent.plan_builder.closed_loop.retry_request_builders import (
    build_retry_request_from_facts_issue,
    build_retry_request_from_material_readiness,
    build_retry_request_from_placement_dependency,
)
from openmc_agent.plan_builder.state import PlanBuildState


def _state() -> PlanBuildState:
    return PlanBuildState(state_id="fidelity", requirement_text="reactor-neutral")


def test_typed_request_is_idempotently_registered() -> None:
    state = _state()
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "facts", "required_ids": ["u1"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    # Re-normalizing the same typed request must NOT create a duplicate.
    request_again = normalize_retry_request(request, state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE)
    assert request_again.request_id == request.request_id
    assert len(state.plan_retry_requests) == 1


def test_same_fingerprint_active_request_does_not_duplicate() -> None:
    state = _state()
    r1 = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "facts", "required_ids": ["u1"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    r2 = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "facts", "required_ids": ["u1"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert r1 is not None and r2 is not None
    assert r1.request_id == r2.request_id
    assert len(state.plan_retry_requests) == 1


def test_placement_dependency_preserves_required_ids_and_dependency_type() -> None:
    state = _state()
    request = build_retry_request_from_placement_dependency(
        dependency_patch_type="universes",
        issue_codes=["localized_insert.required_universe_missing"],
        finding_ids=["finding_1"],
        required_ids=["rcca_aic", "rcca_b4c"],
        reason="placement needs universe",
        state=state,
        downstream_patch_types=["localized_insert_profiles", "assembly_catalog"],
        gate_input_hash="abc123",
    )
    assert request is not None
    assert request.targets[0].required_ids == ["rcca_aic", "rcca_b4c"]
    assert request.targets[0].metadata["dependency_patch_type"] == "universes"
    assert request.gate_input_hash == "abc123"
    assert isinstance(request.owner_patch_hashes, dict)


def test_material_readiness_aggregates_consumers() -> None:
    state = _state()
    request = build_retry_request_from_material_readiness(
        material_id="zircaloy4",
        consumer_ids=["grid_1", "grid_2", "grid_3"],
        required_property="density_g_cm3",
        state=state,
    )
    assert request is not None
    assert request.reason_code == "materials.execution_density_required"
    assert request.consumer_ids == ["grid_1", "grid_2", "grid_3"]
    assert request.targets[0].required_ids == ["zircaloy4"]
    assert request.targets[0].required_properties == ["density_g_cm3"]


def test_facts_issue_preserves_expected_and_current_values() -> None:
    state = _state()
    request = build_retry_request_from_facts_issue(
        issue_code="facts.model_scope_conflicts_with_planning_features",
        affected_json_paths=["/model_scope"],
        finding_ids=["finding_2"],
        state=state,
        expected_value="multi_assembly_core",
        current_value="single_assembly",
    )
    assert request is not None
    assert request.targets[0].metadata["expected_value"] == "multi_assembly_core"
    assert request.targets[0].metadata["current_value"] == "single_assembly"
    assert request.gate_id is not None
    assert request.gate_id.value == "facts"


def test_executable_plan_retry_request_passes_through_idempotently() -> None:
    state = _state()
    built = build_retry_request_from_material_readiness(
        material_id="inconel718", consumer_ids=["grid_8"], required_property="density_g_cm3", state=state,
    )
    assert built is not None
    passed = normalize_retry_request(built, state=state)
    assert passed is not None
    assert passed.request_id == built.request_id
    assert len(state.plan_retry_requests) == 1
