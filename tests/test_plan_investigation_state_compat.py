"""Tests for PlanBuildState backwards-compatibility with plan-investigation slots."""

from __future__ import annotations

import json

from openmc_agent.plan_builder.state import PlanBuildState
from openmc_agent.plan_investigation import PLAN_INVESTIGATION_SCHEMA_VERSION
from openmc_agent.plan_investigation.state_compat import (
    get_plan_investigation_schema_version,
    get_planning_evidence_ledger,
    get_planning_source_manifest,
    has_plan_investigation_state,
    mark_plan_investigation_schema_version,
    set_planning_evidence_ledger,
    set_planning_source_manifest,
)


_LEGACY_CHECKPOINT = {
    "state_id": "pbs_legacy",
    "requirement_text": "req",
    "planning_mode": "incremental",
}


def test_legacy_checkpoint_loads_with_defaults() -> None:
    state = PlanBuildState.model_validate(_LEGACY_CHECKPOINT)
    assert state.planning_source_manifest is None
    assert state.planning_evidence_ledger is None
    assert state.plan_investigation_schema_version is None


def test_helpers_round_trip() -> None:
    state = PlanBuildState.model_validate(_LEGACY_CHECKPOINT)
    assert not has_plan_investigation_state(state)

    set_planning_source_manifest(state, {"sources": ["src_a"]})
    set_planning_evidence_ledger(state, {"ledger_hash": "h"})
    mark_plan_investigation_schema_version(state)

    assert has_plan_investigation_state(state)
    assert get_planning_source_manifest(state) == {"sources": ["src_a"]}
    assert get_planning_evidence_ledger(state) == {"ledger_hash": "h"}
    assert get_plan_investigation_schema_version(state) == PLAN_INVESTIGATION_SCHEMA_VERSION


def test_round_trip_via_json() -> None:
    state = PlanBuildState.model_validate(_LEGACY_CHECKPOINT)
    set_planning_source_manifest(state, {"sources": ["x"]})
    mark_plan_investigation_schema_version(state)

    dump = state.model_dump(mode="json")
    serialized = json.dumps(dump)
    restored = PlanBuildState.model_validate(json.loads(serialized))
    assert get_planning_source_manifest(restored) == {"sources": ["x"]}
    assert get_plan_investigation_schema_version(restored) == PLAN_INVESTIGATION_SCHEMA_VERSION


def test_helpers_reject_wrong_types() -> None:
    state = PlanBuildState.model_validate(_LEGACY_CHECKPOINT)
    import pytest

    with pytest.raises(TypeError):
        set_planning_source_manifest(state, ["not", "a", "dict"])
    with pytest.raises(TypeError):
        set_planning_evidence_ledger(state, 42)


def test_existing_build_log_behavior_unchanged() -> None:
    """Adding the new fields must not perturb existing build_log/event flow."""
    state = PlanBuildState.model_validate(_LEGACY_CHECKPOINT)
    state.add_event("planning.test", "hello")
    assert len(state.build_log) == 1
    assert state.build_log[0].event_type == "planning.test"
    assert state.build_log[0].message == "hello"
