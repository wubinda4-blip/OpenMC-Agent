"""Phase-6 regression tests: ensure Phases 1B-5 features still work."""

from openmc_agent.plan_builder.closed_loop.models import (
    PLAN_CLOSED_LOOP_CONTRACT_VERSION,
    PlanClosedLoopPolicy,
    PlanGateId,
    PlanStageState,
    PlanStageStatus,
)
from openmc_agent.plan_builder.closed_loop.controller import initialize_plan_loop_state
from openmc_agent.plan_builder.state import PlanBuildState


def test_contract_version_is_0_9():
    """Phase 8A Step 6B: contract bumped 0.8 → 0.9 for RETRIEVE_EVIDENCE."""

    assert PLAN_CLOSED_LOOP_CONTRACT_VERSION == "0.9"


def test_assembled_plan_gate_id_exists():
    assert PlanGateId.ASSEMBLED_PLAN.value == "assembled_plan"


def test_policy_supports_assembled_plan_review_mode():
    policy = PlanClosedLoopPolicy(assembled_plan_review_mode="controlled")
    assert policy.assembled_plan_review_mode == "controlled"


def test_legacy_0_7_checkpoint_assembled_migrates_to_pending():
    state = PlanBuildState(state_id="migrate", requirement_text="r")
    state.plan_loop_contract_version = "0.7"
    state.plan_loop_stages["plan_gate_assembled_plan"] = PlanStageState(
        stage_id="plan_gate_assembled_plan", gate_id=PlanGateId.ASSEMBLED_PLAN,
        status=PlanStageStatus.SKIPPED, metadata={"review_not_implemented": True},
    )
    policy = PlanClosedLoopPolicy(mode="advisory", gate_enabled={PlanGateId.ASSEMBLED_PLAN: True})
    initialize_plan_loop_state(state, policy, [])
    assert state.plan_loop_contract_version == "0.9"
    assert state.plan_loop_stages["plan_gate_assembled_plan"].status is PlanStageStatus.PENDING
    assert any(e.event_type == "planning.assembled_plan_gate_migrated_to_0_8" for e in state.build_log)


def test_axial_geometry_still_works():
    state = PlanBuildState(state_id="ax", requirement_text="r")
    state.plan_loop_contract_version = "0.6"
    state.plan_loop_stages["plan_gate_axial_geometry"] = PlanStageState(
        stage_id="plan_gate_axial_geometry", gate_id=PlanGateId.AXIAL_GEOMETRY,
        status=PlanStageStatus.SKIPPED, metadata={"review_not_implemented": True},
    )
    policy = PlanClosedLoopPolicy(mode="advisory", gate_enabled={PlanGateId.AXIAL_GEOMETRY: True})
    initialize_plan_loop_state(state, policy, ["axial_layers"])
    assert state.plan_loop_stages["plan_gate_axial_geometry"].status is PlanStageStatus.PENDING


def test_assembled_plan_trigger_origin_exists():
    from openmc_agent.plan_builder.closed_loop.retry_models import RetryTriggerOrigin
    assert RetryTriggerOrigin.ASSEMBLED_PLAN_GATE.value == "assembled_plan_gate"


def test_off_mode_no_assembled_gate_stage():
    policy = PlanClosedLoopPolicy(mode="off")
    state = PlanBuildState(state_id="off", requirement_text="r")
    created = initialize_plan_loop_state(state, policy, [])
    assert created == []
