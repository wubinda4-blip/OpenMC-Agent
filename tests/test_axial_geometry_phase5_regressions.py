"""Phase-5 regression tests: ensure Phases 1B-4 features still work."""

from openmc_agent.plan_builder.closed_loop.models import (
    PLAN_CLOSED_LOOP_CONTRACT_VERSION,
    PlanClosedLoopPolicy,
    PlanGateId,
    PlanStageState,
    PlanStageStatus,
)
from openmc_agent.plan_builder.closed_loop.controller import initialize_plan_loop_state
from openmc_agent.plan_builder.state import PlanBuildState


def test_contract_version_is_0_7():
    assert PLAN_CLOSED_LOOP_CONTRACT_VERSION == "0.7"


def test_axial_geometry_gate_id_exists():
    assert PlanGateId.AXIAL_GEOMETRY.value == "axial_geometry"


def test_policy_supports_axial_geometry_review_mode():
    policy = PlanClosedLoopPolicy(axial_geometry_review_mode="controlled")
    assert policy.axial_geometry_review_mode == "controlled"


def test_legacy_0_6_checkpoint_axial_migrates_to_pending():
    state = PlanBuildState(state_id="migrate", requirement_text="r")
    state.plan_loop_contract_version = "0.6"
    state.plan_loop_stages["plan_gate_axial_geometry"] = PlanStageState(
        stage_id="plan_gate_axial_geometry", gate_id=PlanGateId.AXIAL_GEOMETRY,
        status=PlanStageStatus.SKIPPED, metadata={"review_not_implemented": True},
    )
    policy = PlanClosedLoopPolicy(mode="advisory", gate_enabled={PlanGateId.AXIAL_GEOMETRY: True})
    initialize_plan_loop_state(state, policy, ["axial_layers"])
    assert state.plan_loop_contract_version == "0.7"
    assert state.plan_loop_stages["plan_gate_axial_geometry"].status is PlanStageStatus.PENDING
    assert any(e.event_type == "planning.axial_geometry_gate_migrated_to_0_7" for e in state.build_log)


def test_material_universe_still_works():
    """Phase 4 contract migration still fires the right event."""
    state = PlanBuildState(state_id="mu", requirement_text="r")
    state.plan_loop_contract_version = "0.5"
    state.plan_loop_stages["plan_gate_material_universe"] = PlanStageState(
        stage_id="plan_gate_material_universe", gate_id=PlanGateId.MATERIAL_UNIVERSE,
        status=PlanStageStatus.SKIPPED, metadata={"review_not_implemented": True},
    )
    policy = PlanClosedLoopPolicy(mode="advisory", gate_enabled={PlanGateId.MATERIAL_UNIVERSE: True})
    initialize_plan_loop_state(state, policy, ["materials"])
    assert state.plan_loop_stages["plan_gate_material_universe"].status is PlanStageStatus.PENDING


def test_off_mode_no_axial_gate_stage():
    policy = PlanClosedLoopPolicy(mode="off")
    state = PlanBuildState(state_id="off", requirement_text="r")
    created = initialize_plan_loop_state(state, policy, ["axial_layers"])
    assert created == []
    assert "plan_gate_axial_geometry" not in state.plan_loop_stages


def test_axial_geometry_trigger_origin_exists():
    from openmc_agent.plan_builder.closed_loop.retry_models import RetryTriggerOrigin
    assert RetryTriggerOrigin.AXIAL_GEOMETRY_GATE.value == "axial_geometry_gate"
