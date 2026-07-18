"""Tests for Assembled Plan controlled barrier requirements."""

from tests._assembled_plan_fixtures import state_with_assembled_plan, make_assembled_plan
from openmc_agent.plan_builder.closed_loop.models import PlanStageStatus


def test_barrier_all_upstream_accepted():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan, upstream_accepted=True)
    for key in ("plan_gate_facts", "plan_gate_material_universe", "plan_gate_placement", "plan_gate_axial_geometry"):
        stage = state.plan_loop_stages[key]
        assert stage.status is PlanStageStatus.ACCEPTED


def test_barrier_facts_not_accepted():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan, upstream_accepted=False)
    facts_stage = state.plan_loop_stages["plan_gate_facts"]
    assert facts_stage.status is not PlanStageStatus.ACCEPTED
