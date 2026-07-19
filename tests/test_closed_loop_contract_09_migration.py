"""Phase 8A Step 6B — closed-loop contract 0.8 → 0.9 migration tests.

Verifies:
* Old 0.8 checkpoints can be loaded.
* The new ``RETRIEVE_EVIDENCE`` action round-trips through JSON.
* ``PLAN_CLOSED_LOOP_CONTRACT_VERSION == "0.9"``.
* Old checkpoints migrated to 0.9 keep their accepted-gate history.
"""

from __future__ import annotations

import json

import pytest

from openmc_agent.plan_builder.closed_loop.models import (
    PLAN_CLOSED_LOOP_CONTRACT_VERSION,
    PlanClosedLoopPolicy,
    PlanGateId,
    PlanReviewAction,
    PlanStageState,
    PlanStageStatus,
)
from openmc_agent.plan_builder.closed_loop.controller import initialize_plan_loop_state
from openmc_agent.plan_builder.state import PlanBuildState


def test_contract_version_is_0_9() -> None:
    assert PLAN_CLOSED_LOOP_CONTRACT_VERSION == "0.9"


def test_policy_accepts_0_9() -> None:
    p = PlanClosedLoopPolicy(contract_version="0.9")
    assert p.contract_version == "0.9"


def test_policy_accepts_0_8_for_backward_compat() -> None:
    """Old 0.8 policies can still be loaded (migration)."""

    p = PlanClosedLoopPolicy(contract_version="0.8")
    assert p.contract_version == "0.8"


def test_policy_rejects_unknown_version() -> None:
    with pytest.raises(Exception):
        PlanClosedLoopPolicy(contract_version="9.9")


def test_retrieve_evidence_action_round_trips() -> None:
    """RETRIEVE_EVIDENCE enum value round-trips through JSON."""

    raw = json.dumps(PlanReviewAction.RETRIEVE_EVIDENCE.value)
    assert json.loads(raw) == "retrieve_evidence"
    # The enum value can be reconstructed from the string.
    reconstructed = PlanReviewAction("retrieve_evidence")
    assert reconstructed is PlanReviewAction.RETRIEVE_EVIDENCE


def test_all_known_actions_still_round_trip() -> None:
    """All pre-existing actions still round-trip (no regression)."""

    expected = {
        "approve", "revise_current_patch", "retry_dependency",
        "ask_human", "fail_closed", "retrieve_evidence",
    }
    actual = {a.value for a in PlanReviewAction}
    assert actual == expected


def test_legacy_0_8_checkpoint_loads_and_migrates() -> None:
    """A 0.8 checkpoint loads and migrates to 0.9 without clearing history."""

    state = PlanBuildState(state_id="migrate", requirement_text="r")
    state.plan_loop_contract_version = "0.8"
    # Simulate an accepted Facts Gate from a 0.8 run.
    state.plan_loop_stages["plan_gate_facts"] = PlanStageState(
        stage_id="plan_gate_facts", gate_id=PlanGateId.FACTS,
        status=PlanStageStatus.ACCEPTED,
        metadata={"accepted_input_hash": "abc"},
    )
    policy = PlanClosedLoopPolicy(mode="advisory", gate_enabled={PlanGateId.FACTS: True})
    initialize_plan_loop_state(state, policy, ["facts"])
    # Contract bumped.
    assert state.plan_loop_contract_version == "0.9"
    # Accepted history preserved.
    facts_stage = state.plan_loop_stages["plan_gate_facts"]
    assert facts_stage.status is PlanStageStatus.ACCEPTED
    assert facts_stage.metadata.get("accepted_input_hash") == "abc"
    # Migration event recorded.
    events = [e.event_type for e in state.build_log]
    assert "planning.retry_protocol_migrated" in events


def test_legacy_0_7_checkpoint_loads_and_migrates_to_0_9() -> None:
    """A 0.7 checkpoint skips through 0.8 and lands at 0.9."""

    state = PlanBuildState(state_id="migrate", requirement_text="r")
    state.plan_loop_contract_version = "0.7"
    policy = PlanClosedLoopPolicy(mode="advisory")
    initialize_plan_loop_state(state, policy, [])
    assert state.plan_loop_contract_version == "0.9"


def test_retrieve_evidence_action_known_in_compute_allowed_actions_fallback() -> None:
    """The action enum is recognised in the closed-loop policy module.

    Previously only 5 actions existed; the new enum value must not
    break any code that iterates ``PlanReviewAction``.
    """

    actions = list(PlanReviewAction)
    assert PlanReviewAction.RETRIEVE_EVIDENCE in actions
    assert len(actions) == 6
