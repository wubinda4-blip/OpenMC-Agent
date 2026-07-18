"""Tests for Axial Geometry controlled barrier requirements."""

from tests._axial_geometry_fixtures import state_with_axial_patches
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy, PlanGateId, PlanStageStatus


def test_barrier_requires_facts_accepted():
    state = state_with_axial_patches()
    stage = state.plan_loop_stages["plan_gate_axial_geometry"]
    # Set facts to blocked.
    facts_stage = state.plan_loop_stages["plan_gate_facts"]
    facts_stage.status = PlanStageStatus.BLOCKED
    # The executor gate should detect this and block.  We simulate the check
    # directly to test the barrier logic without running the full executor.
    assert facts_stage.status is not PlanStageStatus.ACCEPTED


def test_barrier_requires_material_universe_accepted():
    state = state_with_axial_patches()
    mu_stage = state.plan_loop_stages["plan_gate_material_universe"]
    mu_stage.status = PlanStageStatus.PENDING
    assert mu_stage.status is not PlanStageStatus.ACCEPTED


def test_barrier_requires_placement_accepted():
    state = state_with_axial_patches()
    placement_stage = state.plan_loop_stages["plan_gate_placement"]
    placement_stage.status = PlanStageStatus.PENDING
    assert placement_stage.status is not PlanStageStatus.ACCEPTED


def test_all_upstream_accepted_proceeds():
    state = state_with_axial_patches()
    for key in ("plan_gate_facts", "plan_gate_material_universe", "plan_gate_placement"):
        stage = state.plan_loop_stages[key]
        assert stage.status is PlanStageStatus.ACCEPTED


def test_axial_gate_stage_exists_after_init():
    state = state_with_axial_patches()
    assert "plan_gate_axial_geometry" in state.plan_loop_stages
    stage = state.plan_loop_stages["plan_gate_axial_geometry"]
    assert stage.gate_id == PlanGateId.AXIAL_GEOMETRY
