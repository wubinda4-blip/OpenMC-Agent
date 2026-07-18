"""Tests for the controlled-mode investigation barrier.

Verifies that a blocked investigation prevents the Facts patch LLM call
AND the Facts gate from running.
"""

from __future__ import annotations

import json

import pytest

from openmc_agent.plan_builder.executor import run_incremental_planning
from openmc_agent.plan_builder.state import PlanBuildState
from openmc_agent.plan_investigation.executor_injection import (
    BLOCK_CODE_FACTS_BLOCKED,
    EVENT_INVESTIGATION_BLOCKED,
)
from openmc_agent.plan_investigation.runner import (
    BLOCK_CODE_CLIENT_UNAVAILABLE,
    PlanInvestigationConfig,
    PlanInvestigationMode,
)


def _state():
    return PlanBuildState.model_validate(
        {
            "state_id": "pbs_test",
            "requirement_text": "requirement text",
            "planning_mode": "incremental",
        }
    )


def test_blocked_investigation_records_event() -> None:
    state = _state()
    patch_calls = []

    result = run_incremental_planning(
        requirement="requirement text",
        state=state,
        llm_client=lambda p: patch_calls.append(p) or json.dumps({"patch_type": "facts"}),
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
        plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED),
        plan_investigation_client=None,
    )
    assert EVENT_INVESTIGATION_BLOCKED in [e.event_type for e in state.build_log]
    assert patch_calls == []


def test_blocked_investigation_carries_block_code() -> None:
    state = _state()
    result = run_incremental_planning(
        requirement="requirement text",
        state=state,
        llm_client=lambda p: json.dumps({"patch_type": "facts"}),
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
        plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED),
        plan_investigation_client=None,
    )
    assert (
        result.summary.get("investigation_block_code")
        == BLOCK_CODE_CLIENT_UNAVAILABLE
    )


def test_blocked_investigation_disposition() -> None:
    state = _state()
    result = run_incremental_planning(
        requirement="requirement text",
        state=state,
        llm_client=lambda p: json.dumps({"patch_type": "facts"}),
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
        plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED),
        plan_investigation_client=None,
    )
    outcome = result.plan_loop_outcome
    assert outcome["status"] == "blocked"
    assert outcome["active_gate_id"] == "facts"
    assert "BLOCKED_BY_INVESTIGATION:facts" in outcome["detail"]


def test_blocked_investigation_prevents_facts_gate_run() -> None:
    """When the investigation blocks, the Facts Gate must NOT run."""
    state = _state()
    # The build_log should NOT contain any facts-gate events because
    # the run blocked before reaching the gate.
    result = run_incremental_planning(
        requirement="requirement text",
        state=state,
        llm_client=lambda p: json.dumps({"patch_type": "facts"}),
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
        plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED),
        plan_investigation_client=None,
    )
    facts_gate_events = [
        e for e in state.build_log
        if "facts_gate" in e.event_type or "facts_review" in e.event_type
    ]
    assert facts_gate_events == []
