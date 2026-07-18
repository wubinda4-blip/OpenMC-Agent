"""Tests for Assembled Plan Gate modes (off / advisory / controlled)."""

from tests._assembled_plan_fixtures import state_with_assembled_plan, make_assembled_plan
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy, PlanGateId, PlanLoopMode


def test_off_mode_default():
    policy = PlanClosedLoopPolicy()
    assert policy.assembled_plan_review_mode == "off"


def test_controlled_mode_declarable():
    policy = PlanClosedLoopPolicy(mode=PlanLoopMode.CONTROLLED, assembled_plan_review_mode="controlled")
    assert policy.assembled_plan_review_mode == "controlled"


def test_advisory_mode_declarable():
    policy = PlanClosedLoopPolicy(mode=PlanLoopMode.ADVISORY, assembled_plan_review_mode="advisory")
    assert policy.assembled_plan_review_mode == "advisory"


def test_gate_stage_exists():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan)
    assert "plan_gate_assembled_plan" in state.plan_loop_stages
    stage = state.plan_loop_stages["plan_gate_assembled_plan"]
    assert stage.gate_id == PlanGateId.ASSEMBLED_PLAN
